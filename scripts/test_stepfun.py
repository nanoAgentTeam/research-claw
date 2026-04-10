import asyncio
import os
import sys
from openai import AsyncOpenAI
import time
from pathlib import Path

# Add current directory to path
sys.path.append(os.getcwd())

from config.loader import load_config
from agent.loop import AgentLoop
from bus.queue import MessageBus

async def test_stepfun(streaming=True):
    config = load_config()
    api_key = config.get_api_key()
    api_base = config.get_api_base()
    model = config.get_api_model() or "step-3.5-flash"

    # Initialize Real Agent to get real tools
    from bus.queue import MessageBus
    class MockProvider:
        def __init__(self):
            self.api_key = "test"
            self.api_base = "test"

    workspace = Path(os.getcwd())
    agent = AgentLoop(bus=MessageBus(), provider=MockProvider(), workspace=workspace, model="test")
    tool_defs = agent.tools.get_definitions()
    system_prompt = agent.context.build_system_prompt()

    print(f"\n--- Testing StepFun API (Realistic) ---")
    print(f"Model: {model}")
    print(f"Base URL: {api_base}")
    print(f"Streaming: {streaming}")
    print(f"Tool Count: {len(tool_defs)}")

    client = AsyncOpenAI(api_key=api_key, base_url=api_base)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "hello"}
    ]

    kwargs = {
        "model": model,
        "messages": messages,
        "stream": streaming,
        "tools": tool_defs,
        "tool_choice": "auto",
        "max_tokens": 8192,
        "temperature": 0.7
    }

    start_time = time.time()
    try:
        print("Sending request...")
        if streaming:
            stream = await client.chat.completions.create(**kwargs)
            print("Stream object received, iterating...")
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    print(chunk.choices[0].delta.content, end="", flush=True)
            print("\nStream finished.")
        else:
            response = await client.chat.completions.create(**kwargs)
            print(f"Response: {response.choices[0].message.content}")

        print(f"Done in {time.time() - start_time:.2f}s")
    except Exception as e:
        print(f"\nError: {e}")

if __name__ == "__main__":
    asyncio.run(test_stepfun(streaming=True))
