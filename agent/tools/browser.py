import asyncio
from pathlib import Path
from typing import Any, Callable, Dict, Optional, TypeVar, Union

from browser_use import BrowserProfile, Controller
from browser_use.agent.service import Agent
from browser_use.agent.views import AgentHistoryList, ActionResult
from browser_use.browser import BrowserSession
from browser_use.browser.events import NavigateToUrlEvent
from pydantic import Field, BaseModel
from core.prompts import render as render_prompt
from browser_use.llm.base import BaseChatModel
from browser_use.llm.openai.chat import ChatOpenAI
from browser_use.llm.messages import BaseMessage
from browser_use.llm.views import ChatInvokeCompletion
from browser_use.llm.openai.serializer import OpenAIMessageSerializer
from browser_use.llm.exceptions import ModelProviderError
from typing import TypeVar, Any
import json
import re

T = TypeVar('T')

class CustomSearchAction(BaseModel):
    query: str
    engine: str = Field(
        default='google', description='google, duckduckgo, bing (use google by default)'
    )

class RobustChatOpenAI(ChatOpenAI):
    """
    A subclass of ChatOpenAI that attempts to extract JSON from the response
    if strict parsing fails. Useful for models that are 'chatty' or include
    XML/thinking process in their output.
    """
    async def ainvoke(
        self, messages: list[BaseMessage], output_format: Optional[type[T]] = None, **kwargs: Any
    ) -> Union[ChatInvokeCompletion[T], ChatInvokeCompletion[str]]:
        # If no output format, just use parent
        if output_format is None:
            return await super().ainvoke(messages, output_format, **kwargs)

        # If we are forcing structured output via API, parent handles it.
        # But for StepFun we set dont_force_structured_output=True, so parent gets a string
        # and tries to parse it directly.

        # We can't easily intercept the parent's internal call without copying code.
        # So we will replicate the critical part of parent's ainvoke logic for the
        # dont_force_structured_output=True case, but with robust parsing.

        if not self.dont_force_structured_output:
            return await super().ainvoke(messages, output_format, **kwargs)

        # --- Custom Logic for dont_force_structured_output=True ---
        openai_messages = OpenAIMessageSerializer.serialize_messages(messages)
        model_params = self._get_model_params() # Helper to get params

        # Add schema to system prompt if needed (logic from parent)
        if self.add_schema_to_system_prompt and openai_messages and openai_messages[0]['role'] == 'system':
             # We need the schema. Parent creates it using SchemaOptimizer.
             from browser_use.llm.schema import SchemaOptimizer
             schema = SchemaOptimizer.create_optimized_json_schema(
                output_format,
                remove_min_items=self.remove_min_items_from_schema,
                remove_defaults=self.remove_defaults_from_schema,
             )
             response_format = {
                'name': 'agent_output',
                'strict': True,
                'schema': schema,
             }
             schema_text = f'\n<json_schema>\n{json.dumps(response_format, ensure_ascii=False)}\n</json_schema>'
             if isinstance(openai_messages[0]['content'], str):
                openai_messages[0]['content'] += schema_text
             # Ignore Iterable case for simplicity or handle if needed

        try:
            # Call client directly
            response = await self.get_client().chat.completions.create(
                model=self.model,
                messages=openai_messages,
                **model_params,
            )

            content = response.choices[0].message.content or ""

            # Try to extract JSON if it looks like there is extra text
            json_str = self._extract_json(content)
            if not json_str:
                # If we can't find JSON structure, try to parse content directly (it might fail)
                json_str = content

            try:
                parsed = output_format.model_validate_json(json_str)
                # Success!
                from browser_use.llm.views import ChatInvokeUsage
                usage = self._get_usage(response)
                return ChatInvokeCompletion(
                    completion=parsed,
                    usage=usage,
                    stop_reason=response.choices[0].finish_reason if response.choices else None,
                )
            except Exception as parse_error:
                 # If parse fails, raise ModelProviderError
                 raise ModelProviderError(message=f"Failed to parse JSON: {parse_error}. Original content: {content}", model=self.name) from parse_error

        except Exception as e:
            # If it's already our error, re-raise
            if isinstance(e, ModelProviderError):
                raise e
            # Otherwise wrap it
            raise ModelProviderError(message=str(e), model=self.name) from e

    def _extract_json(self, text: str) -> Optional[str]:
        # 1. Try to find XML tool call format first (more specific to StepFun)
        xml_match = re.search(r'<tool_call>\s*<function=(?P<name>\w+)>(?P<content>.*?)</function>\s*</tool_call>', text, re.DOTALL)
        if xml_match:
            function_name = xml_match.group('name')
            content = xml_match.group('content')

            params = {}
            # Extract parameters
            param_matches = re.finditer(r'<parameter=(?P<key>\w+)>(?P<value>.*?)</parameter>', content, re.DOTALL)

            for match in param_matches:
                key = match.group('key')
                value = match.group('value').strip()

                # Try to convert value to appropriate type
                if value.lower() == 'true':
                    value = True
                elif value.lower() == 'false':
                    value = False
                elif value == '[]':
                    value = []
                elif value.startswith('[') and value.endswith(']'):
                    try:
                        value = json.loads(value)
                    except:
                        pass # Keep as string if not valid JSON
                elif value.isdigit():
                    value = int(value)
                elif value.replace('.', '', 1).isdigit() and value.count('.') < 2:
                    try:
                        value = float(value)
                    except ValueError:
                        pass # Keep as string

                params[key] = value

            # --- Fix 1: Intercept 'think' function ---
            # If the model called <function=think>, we move its 'thought' parameter to the 'thinking' field
            # and provide a default 'wait' action since browser-use requires at least one action.
            thinking_text = "XML output parsed"
            actual_action = {function_name: params}

            if function_name == 'think':
                thinking_text = params.get('thought', params.get('thinking', str(params)))
                actual_action = {"wait": {"seconds": 1}} # No-op action

            # Construct the JSON structure expected by browser-use (flattened)
            json_structure = {
                "thinking": thinking_text,
                "evaluation_previous_goal": "XML output parsed",
                "memory": "Parsed from XML <tool_call>",
                "next_goal": f"Execute {function_name}",
                "action": [actual_action]
            }
            return json.dumps(json_structure)

        # 2. Try to find JSON object
        # We look for the largest substring that is valid JSON
        start_idx = 0
        while True:
            start = text.find('{', start_idx)
            if start == -1:
                break

            end_idx = len(text)
            while True:
                end = text.rfind('}', start, end_idx)
                if end == -1 or end <= start:
                    break

                json_str = text[start:end+1]
                try:
                    data = json.loads(json_str)
                    # If we got here, it's valid JSON!

                    # --- Apply Fixes ---
                    # 1. Handle nested 'current_state'
                    if 'current_state' in data and isinstance(data['current_state'], dict):
                        cs = data.pop('current_state')
                        for k, v in cs.items():
                            if k not in data:
                                data[k] = v

                    # 2. Handle 'action' normalization
                    if 'action' in data and isinstance(data['action'], list):
                        new_actions = []
                        for act in data['action']:
                            if isinstance(act, dict):
                                # Check if it's like {"evaluate": "code..."} instead of {"evaluate": {"code": "code..."}}
                                # StepFun often simplifies the object structure
                                for key, val in list(act.items()):
                                    if isinstance(val, str) and key != 'index':
                                        param_map = {
                                            'evaluate': 'code',
                                            'navigate': 'url',
                                            'search': 'query',
                                            'input': 'text',
                                            'scroll': 'index',
                                            'done': 'text',
                                            'extract': 'query'
                                        }
                                        if key in param_map:
                                            act[key] = {param_map[key]: val}

                                    # --- Fix 2: Force type conversion for specific fields ---
                                    # Convert numeric tab_id to string to satisfy Pydantic validation
                                    if key in ('switch', 'close') and isinstance(val, dict):
                                        if 'tab_id' in val and not isinstance(val['tab_id'], str):
                                            val['tab_id'] = str(val['tab_id'])

                                    # Special fix for 'done' which StepFun often gets wrong
                                    if key == 'done' and isinstance(val, dict):
                                        # Ensure 'files_to_display' is a list, not a string "[]"
                                        if 'files_to_display' in val and isinstance(val['files_to_display'], str):
                                            if val['files_to_display'] == '[]':
                                                val['files_to_display'] = []
                                            else:
                                                try:
                                                    val['files_to_display'] = json.loads(val['files_to_display'])
                                                except:
                                                    val['files_to_display'] = [val['files_to_display']]

                                new_actions.append(act)
                            elif isinstance(act, str):
                                new_actions.append({act: {}})
                        data['action'] = new_actions

                    # 3. Ensure required fields
                    for req in ['evaluation_previous_goal', 'memory', 'next_goal']:
                        if req not in data:
                            data[req] = "Not provided"

                    if 'action' not in data or not data['action']:
                        data['action'] = [{"wait": {"seconds": 1}}]

                    return json.dumps(data)
                except json.JSONDecodeError:
                    # Not valid JSON, try a smaller end index
                    end_idx = end
                    continue

            start_idx = start + 1

        return None

    def _get_model_params(self):
        # Helper to reconstruct model params since they are not public in parent
        params = {}
        if self.temperature is not None: params['temperature'] = self.temperature
        if self.frequency_penalty is not None: params['frequency_penalty'] = self.frequency_penalty
        if self.max_completion_tokens is not None: params['max_completion_tokens'] = self.max_completion_tokens
        if self.top_p is not None: params['top_p'] = self.top_p
        if self.seed is not None: params['seed'] = self.seed
        # ... others ignored for now
        return params

try:
    import nest_asyncio  # type: ignore[import]
except ImportError:  # pragma: no cover
    nest_asyncio = None

from loguru import logger as Logger
from core.tools.base import BaseTool

DEFAULT_MAX_STEPS = 30


class BrowserUseTool(BaseTool):
    """
    A bridge to the browser-use agent for executing multi-step browsing tasks.
    """

    def __init__(self, provider_key: Optional[str] = None, llm: Optional[BaseChatModel] = None, work_dir: Optional[Path] = None, config: Optional[Any] = None):
        from config.schema import Config as AppConfig
        self.config = config or AppConfig()

        # Determine provider: prefer active LLM from config, else step, else openai
        active_llm = self.config.get_active_provider()
        if active_llm and active_llm.api_key:
            self.provider_key = active_llm.provider
        elif self.config.providers.step.api_key:
            self.provider_key = "step"
        elif provider_key:
            self.provider_key = provider_key
        else:
            self.provider_key = "openai"

        self.llm = llm or self._create_llm()
        self.work_dir = work_dir

    def _create_llm(self) -> Optional[BaseChatModel]:
        # Try new unified llm config first
        active_llm = self.config.get_active_provider()
        if active_llm and active_llm.api_key:
            model = active_llm.model_name or "gpt-4"
            api_key = active_llm.api_key
            base_url = active_llm.api_base
        else:
            # Fallback to legacy providers config
            provider_cfg = getattr(self.config.providers, self.provider_key, None)
            if not provider_cfg or not provider_cfg.api_key:
                return None
            model = provider_cfg.model or "gpt-4"
            api_key = provider_cfg.api_key
            base_url = provider_cfg.api_base

        llm_kwargs = {}
        if base_url:
            llm_kwargs["base_url"] = base_url

        # Step models need special handling
        is_step = self.provider_key == "step" or (model and model.startswith("step"))
        if is_step:
            llm_kwargs["dont_force_structured_output"] = True
            llm_kwargs["add_schema_to_system_prompt"] = True
            llm_kwargs["timeout"] = 1200.0
            llm_kwargs["max_retries"] = 3
            return RobustChatOpenAI(model=model, api_key=api_key, **llm_kwargs)

        return ChatOpenAI(model=model, api_key=api_key, **llm_kwargs)

    @property
    def name(self) -> str:
        return "browser_use"

    @property
    def description(self) -> str:
        return (
            "使用浏览器执行自动化任务（基于 browser-use 库）。适用于需要真实浏览器交互的复杂任务，"
            "如点击、滚动、处理动态内容、多页跳转和结构化数据提取。"
            "注意：传递给此工具的任务描述（task）应尽可能简洁直接（例如：'2025.1.8的金价是多少'），"
            "避免包含冗长的引导语或复杂的背景说明，这样更有利于工具内部进行精准搜索。"
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "简洁直接的任务描述。例如：'2025.1.8金价' 而不是 '请帮我查一下2025年1月8日的黄金价格是多少'。"
                },
                "max_steps": {
                    "type": "integer",
                    "description": "Maximum agent steps before giving up.",
                    "minimum": 1,
                    "default": DEFAULT_MAX_STEPS
                },
                "headless": {
                    "type": "boolean",
                    "description": "Whether to run in headless mode. Set to true for background execution, false to see the browser.",
                    "default": True
                }
            },
            "required": ["task"]
        }

    def get_status_message(self, **kwargs) -> str:
        task = kwargs.get("task", "")
        return f"\n\n🌐 **正在启动浏览器执行任务**: {task[:100]}...\n"

    async def execute_async(self, task: str, max_steps: int = DEFAULT_MAX_STEPS, headless: Optional[bool] = None, on_token: Optional[Callable[[str], None]] = None) -> str:
        """
        Run the browser-use Agent asynchronously and return the final extracted text.
        """
        if headless is None:
            headless = self.config.features.tools.browser_headless

        if self.llm is None:
            return f"LLM for provider '{self.provider_key}' is not configured. Check settings."

        max_steps = max(max_steps, 1)

        # Configure Browser Profile
        # Optimization: Reduce iframes and block common ad/tracking domains to improve stability
        # Use work_dir for downloads if provided
        profile_kwargs = {
            "headless": headless,
            "disable_security": True,
            "max_iframes": 40,
            "cross_origin_iframes": False,
            "prohibited_domains": [
                "google-analytics.com", "doubleclick.net", "adsystem.com",
                "facebook.net", "googlesyndication.com", "adnxs.com",
                "quantserve.com", "scorecardresearch.com"
            ]
        }

        if self.work_dir:
            downloads_dir = self.work_dir / "resources"
            downloads_dir.mkdir(parents=True, exist_ok=True)
            profile_kwargs["downloads_path"] = str(downloads_dir)
            Logger.info(f"BrowserUseTool: downloads will be saved to {downloads_dir}")

        profile = BrowserProfile(**profile_kwargs)

        # Detect if we should disable vision (e.g. for StepFun text-only models)
        use_vision = True
        is_step_model = self.provider_key == "step"

        if is_step_model:
            # StepFun models (like step-3.5-flash) might not support image input
            use_vision = False
            Logger.info(f"Disabling vision for provider '{self.provider_key}'.")

        try:
            # Create a controller to override default tools if needed
            controller = Controller()

            # Override search to use Google by default
            @controller.registry.action(
                '',
                param_model=CustomSearchAction,
            )
            async def search(params: CustomSearchAction, browser_session: BrowserSession):
                import urllib.parse
                encoded_query = urllib.parse.quote_plus(params.query)
                search_engines = {
                    'duckduckgo': f'https://duckduckgo.com/?q={encoded_query}',
                    'google': f'https://www.google.com/search?q={encoded_query}&udm=14',
                    'bing': f'https://www.bing.com/search?q={encoded_query}',
                }
                if params.engine.lower() not in search_engines:
                    return ActionResult(error=f'Unsupported search engine: {params.engine}')

                search_url = search_engines[params.engine.lower()]
                try:
                    event = browser_session.event_bus.dispatch(NavigateToUrlEvent(url=search_url, new_tab=False))
                    await event
                    await event.event_result(raise_if_any=True, raise_if_none=False)
                    memory = f"Searched {params.engine.title()} for '{params.query}'"
                    return ActionResult(extracted_content=memory, long_term_memory=memory)
                except Exception as e:
                    return ActionResult(error=f'Failed to search {params.engine}: {str(e)}')

            # Agent will create a browser instance using the profile
            # Force JSON output via system prompt extension for models that might struggle or output XML
            extra_instructions = ""
            if is_step_model:
                _BROWSER_JSON_FALLBACK = """
### ‼️ 强制响应格式 ‼️
你必须**仅**输出一个符合以下结构的 JSON 对象。
**严禁**使用 `<tool_call>` 或 `<function>` 等 XML 标签。
**严禁**在 JSON 之外添加任何解释性文字。

响应模板示例：
{{
  "current_state": {{
    "evaluation_previous_goal": "对上一步的评价",
    "memory": "目前的记忆和进度",
    "next_goal": "下一步的目标"
  }},
  "thought": "你的思考过程",
  "action": [
    {{
      "search": {{
        "query": "搜索关键词"
      }}
    }}
  ]
}}
"""
                extra_instructions = render_prompt("tool_browser_force_json.txt", _BROWSER_JSON_FALLBACK)

            # Define a callback to capture agent steps
            def step_callback(state, model_output, step_number):
                if on_token:
                    msg = f"\n`[🌐 Browser Step {step_number}]`: "
                    if model_output and model_output.action:
                        # Extract action details
                        try:
                            action_dict = model_output.action[0].model_dump(exclude_none=True)
                            action_name = list(action_dict.keys())[0]
                            action_params = action_dict[action_name]

                            if action_name == 'navigate' and 'url' in action_params:
                                msg += f"Navigating to {action_params['url']}"
                            elif action_name == 'search' and 'query' in action_params:
                                msg += f"Searching for '{action_params['query']}'"
                            elif action_name == 'click' and 'index' in action_params:
                                msg += f"Clicking element {action_params['index']}"
                            else:
                                msg += f"Executing {action_name}"
                        except:
                            msg += "Executing action"
                    elif model_output and model_output.thinking:
                        msg += f"{model_output.thinking[:100]}..."
                    else:
                        msg += "Processing..."
                    on_token(msg + "\n")

            agent = Agent(
                task=task,
                llm=self.llm,
                browser_profile=profile,
                controller=controller,
                use_vision=use_vision,
                use_judge=False if is_step_model else True, # Disable judge for step models as in reference
                llm_timeout=1200 if is_step_model else None,
                step_timeout=3000 if is_step_model else 120,
                extend_system_message=extra_instructions if extra_instructions else None,
                register_new_step_callback=step_callback
            )
            history = await agent.run(max_steps=max_steps)

            # Build a comprehensive summary of the browsing process to avoid calling agent retrying
            trace_summary = self._summarize_history(history)
            final_output = history.final_result()

            # Automatically consume 5 steps in the calling agent's loop
            consume_tag = "<consume_steps>5</consume_steps>"

            # Check for downloaded files
            downloads_hint = ""
            if self.work_dir:
                dl_dir = self.work_dir / "resources"
                if dl_dir.exists():
                    files = [f.name for f in dl_dir.iterdir() if f.is_file()]
                    if files:
                        downloads_hint = f"\n\n**Downloaded files** (in `resources/`): {', '.join(files)}"

            if final_output:
                return f"### Browser Task Completed\n**Result**: {final_output}\n\n**Execution Trace**:\n{trace_summary}{downloads_hint}\n\n{consume_tag}"

            errors = [error for error in history.errors() if error]
            if errors:
                return f"### Browser Task Failed/Incomplete\n**Errors**: {'; '.join(errors)}\n\n**Execution Trace**:\n{trace_summary}{downloads_hint}\n\n{consume_tag}"

            return f"### Browser Task Summary\n{trace_summary}{downloads_hint}\n\n{consume_tag}"

        except Exception as exc:  # pragma: no cover - external dependency
            Logger.error(f"BrowserUseTool failed for provider '{self.provider_key}': {exc}")
            return f"Error executing browser task: {exc}"
        # Agent handles browser cleanup automatically when it created it

    def execute(self, task: str, max_steps: Optional[int] = None, headless: bool = True, on_token: Optional[Callable[[str], None]] = None, **kwargs) -> str:
        """
        Synchronous wrapper around execute_async for compatibility with sync tool flows.
        """
        coroutine = self.execute_async(task, max_steps if max_steps is not None else DEFAULT_MAX_STEPS, headless=headless, on_token=on_token)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)

        # If we are in an async loop, we should await it if the caller supports it.
        # But BaseTool.execute signature is synchronous (or at least not explicitly async in base).
        # However, ToolRegistry checks inspect.isawaitable.
        # So we can just return the coroutine!
        return coroutine

    def _summarize_history(self, history: AgentHistoryList) -> str:
        """
        Summarize the full browsing history to provide context to the calling agent.
        """
        if not history.history:
            return "No browsing history recorded."

        steps_summary = []
        for i, step in enumerate(history.history):
            action_desc = "Unknown action"
            if step.model_output and step.model_output.action:
                try:
                    # Collect all actions in this step
                    actions = []
                    for act in step.model_output.action:
                        act_dict = act.model_dump(exclude_none=True)
                        name = list(act_dict.keys())[0]
                        params = act_dict[name]
                        if name == 'navigate': actions.append(f"Navigated to {params.get('url')}")
                        elif name == 'search': actions.append(f"Searched for '{params.get('query')}'")
                        elif name == 'click': actions.append(f"Clicked element {params.get('index')}")
                        elif name == 'input_text': actions.append(f"Input text into {params.get('index')}")
                        elif name == 'done': actions.append(f"Finished: {params.get('text')}")
                        else: actions.append(f"Executed {name}")
                    action_desc = "; ".join(actions)
                except:
                    action_desc = "Executed browser action"

            result_desc = ""
            if step.result:
                # Use the last result in the step (usually there's only one)
                res = step.result[-1]
                if res.error:
                    result_desc = f" (Result: Error - {res.error[:100]})"
                elif res.extracted_content:
                    content = res.extracted_content[:100].replace('\n', ' ')
                    result_desc = f" (Result: Extracted '{content}...') "
                else:
                    result_desc = " (Result: Success)"

            steps_summary.append(f"{i+1}. {action_desc}{result_desc}")

        # Limit to last 15 steps if history is too long to prevent context overflow
        if len(steps_summary) > 15:
            steps_summary = ["... (earlier steps omitted)"] + steps_summary[-15:]

        return "\n".join(steps_summary)

