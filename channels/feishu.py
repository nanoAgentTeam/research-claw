"""Feishu (Lark) channel implementation using WebSocket."""

import asyncio
import json
import threading
from typing import Any, Optional, Union

from loguru import logger
import lark_oapi as lark
from lark_oapi.ws import Client as WSClient
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody, P2ImMessageReceiveV1, PatchMessageRequest, PatchMessageRequestBody

from bus.events import OutboundMessage
from bus.queue import MessageBus
from channels.base import BaseChannel
from config.schema import FeishuConfig
import time
import re


class FeishuChannel(BaseChannel):
    """
    Feishu channel using WebSocket (no public IP needed).
    
    Uses the official lark-oapi SDK.
    Supports streaming responses via message patching.
    """
    
    name = "im_feishu"
    
    def __init__(self, config: FeishuConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: FeishuConfig = config
        self._ws_client: Optional[WSClient] = None
        self._api_client: Optional[lark.Client] = None
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws_thread: Optional[threading.Thread] = None

        # Streaming support
        self._message_buffers: dict[str, dict[str, Any]] = {}  # chat_id -> {message_id, content, dirty, last_flush}
        self._flush_task: Optional[asyncio.Task] = None
        
    async def start(self) -> None:
        """Start the Feishu WebSocket client."""
        if not self.config.app_id or not self.config.app_secret:
            logger.error("Feishu app_id or app_secret not configured")
            return
            
        logger.info(f"Starting Feishu channel (App ID: {self.config.app_id})...")
        self._running = True
        
        # Store the main loop for scheduling tasks from the WS thread
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("No running event loop found, Feishu callbacks might fail")
        
        # Initialize API client for sending messages
        self._api_client = lark.Client.builder() \
            .app_id(self.config.app_id) \
            .app_secret(self.config.app_secret) \
            .log_level(lark.LogLevel.INFO) \
            .build()
            
        # Initialize Event Handler
        event_handler = EventDispatcherHandler.builder("", "") \
            .register_p2_im_message_receive_v1(self._handle_im_message) \
            .build()

        # Initialize WebSocket client for receiving events
        try:
            self._ws_client = WSClient(
                app_id=self.config.app_id,
                app_secret=self.config.app_secret,
                event_handler=event_handler,
                log_level=lark.LogLevel.INFO
            )
        except Exception as e:
            logger.error(f"Failed to initialize Feishu WS client: {e}")
            raise e
            
        # Start WebSocket client in a separate thread
        def run_ws():
            try:
                # Create a fresh event loop for this thread to avoid
                # "This event loop is already running" when hot-reloading
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._ws_client.start()
            except Exception as e:
                logger.error(f"Feishu WebSocket thread error: {e}")
                
        self._ws_thread = threading.Thread(target=run_ws, daemon=True)
        self._ws_thread.start()
        
        # Start flush task
        self._flush_task = asyncio.create_task(self._flush_loop())
        
        logger.info("Feishu WebSocket client started in background thread")

    def _preprocess_markdown(self, text: str) -> str:
        """
        Preprocess Markdown for Feishu Cards.
        Feishu Markdown component often fails to render '#' headers correctly.
        We convert them to bold text for better compatibility.
        """
        if not text:
             return ""
             
        # Replace headers (e.g. "## Title") with bold ("**Title**")
        # (?m) enables multiline matching, ^ matches start of line
        text = re.sub(r'(?m)^\s*#{1,6}\s+(.+)$', r'**\1**', text)
        
        # Replace \[ and \] with $ (LaTeX) - Feishu supports LaTeX via $
        # But we need to be careful not to break other things. 
        # For now, just fix the header issue as requested.
        return text

    async def stop(self) -> None:
        """Stop the Feishu channel."""
        self._running = False
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        # lark-oapi doesn't expose a clean stop method yet, rely on daemon thread
        logger.info("Stopping Feishu channel...")

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through Feishu."""
        if not self._api_client:
            logger.warning("Feishu client not initialized")
            return

        try:
            # Send media files first
            for media_path in (msg.media or []):
                try:
                    await self._send_media_file(msg.chat_id, media_path)
                except Exception as exc:
                    logger.error(f"Error sending Feishu media {media_path}: {exc}")

            if msg.is_chunk:
                await self._handle_chunk(msg)
            else:
                await self._finalize_message(msg)

        except Exception as e:
            logger.error(f"Error sending Feishu message: {e}")

    def _download_message_resource(self, message_id: str, file_key: str,
                                    resource_type: str, file_name: str = "") -> Optional[str]:
        """Download a file/image from a Feishu message to the local media directory.

        Uses the lark SDK ``im.v1.message_resource.get`` API.
        Returns the local file path on success, or None on failure.
        Runs synchronously (called from the SDK's background thread).
        """
        from pathlib import Path
        import mimetypes

        if not self._api_client:
            logger.warning("Feishu API client not initialised, cannot download resource")
            return None

        try:
            from lark_oapi.api.im.v1 import GetMessageResourceRequest

            req = (
                GetMessageResourceRequest.builder()
                .message_id(message_id)
                .file_key(file_key)
                .type(resource_type)
                .build()
            )

            resp = self._api_client.im.v1.message_resource.get(req)
            if not resp.success():
                logger.error(f"Feishu resource download failed: {resp.code} - {resp.msg}")
                return None

            # Determine file extension
            if file_name and "." in file_name:
                ext = "." + file_name.rsplit(".", 1)[-1]
            elif resource_type == "image":
                ext = ".jpg"
            elif resource_type == "audio":
                ext = ".ogg"
            else:
                ext = ""

            media_dir = Path.home() / ".open_research_claw" / "media"
            media_dir.mkdir(parents=True, exist_ok=True)
            save_name = f"{file_key[:16]}{ext}" if not file_name else f"{file_key[:8]}_{file_name}"
            save_path = media_dir / save_name

            with open(save_path, "wb") as f:
                f.write(resp.file.read())

            logger.debug(f"Feishu resource downloaded to {save_path}")
            return str(save_path)

        except Exception as e:
            logger.error(f"Failed to download Feishu resource ({resource_type}): {e}")
            return None

    async def _send_media_file(self, chat_id: str, file_path: str) -> None:
        """Upload and send a file via Feishu IM API."""
        from pathlib import Path
        import os

        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"file not found: {file_path}")

        ext = path.suffix.lower()
        image_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

        receive_id_type = "chat_id" if chat_id.startswith("oc_") else "open_id"

        if ext in image_exts:
            # Upload as image, send as image message
            from lark_oapi.api.im.v1 import CreateImageRequest, CreateImageRequestBody
            img_req = CreateImageRequest.builder().request_body(
                CreateImageRequestBody.builder()
                .image_type("message")
                .image(open(path, "rb"))
                .build()
            ).build()
            img_resp = await asyncio.to_thread(self._api_client.im.v1.image.create, img_req)
            if not img_resp.success():
                raise RuntimeError(f"Feishu image upload failed: {img_resp.code} - {img_resp.msg}")
            image_key = img_resp.data.image_key
            content = json.dumps({"image_key": image_key})
            request = CreateMessageRequest.builder() \
                .receive_id_type(receive_id_type) \
                .request_body(CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("image")
                    .content(content)
                    .build()) \
                .build()
            resp = await asyncio.to_thread(self._api_client.im.v1.message.create, request)
            if not resp.success():
                raise RuntimeError(f"Feishu image send failed: {resp.code} - {resp.msg}")
        else:
            # Upload as file, send as file message
            from lark_oapi.api.im.v1 import CreateFileRequest, CreateFileRequestBody
            file_type = "pdf" if ext == ".pdf" else "doc" if ext in {".doc", ".docx"} else "xls" if ext in {".xls", ".xlsx"} else "ppt" if ext in {".ppt", ".pptx"} else "stream"
            file_req = CreateFileRequest.builder().request_body(
                CreateFileRequestBody.builder()
                .file_type(file_type)
                .file_name(path.name)
                .file(open(path, "rb"))
                .build()
            ).build()
            file_resp = await asyncio.to_thread(self._api_client.im.v1.file.create, file_req)
            if not file_resp.success():
                raise RuntimeError(f"Feishu file upload failed: {file_resp.code} - {file_resp.msg}")
            file_key = file_resp.data.file_key
            content = json.dumps({"file_key": file_key})
            request = CreateMessageRequest.builder() \
                .receive_id_type(receive_id_type) \
                .request_body(CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("file")
                    .content(content)
                    .build()) \
                .build()
            resp = await asyncio.to_thread(self._api_client.im.v1.message.create, request)
            if not resp.success():
                raise RuntimeError(f"Feishu file send failed: {resp.code} - {resp.msg}")

        logger.info(f"Feishu media sent to {chat_id}: {file_path}")

    async def _handle_chunk(self, msg: OutboundMessage) -> None:
        """Handle a message chunk."""
        chat_id = msg.chat_id
        content = msg.content
        stream_id = getattr(msg, "stream_id", None) or "main"
        
        # If new_message flag is set, force start a new message bubble
        # This is crucial for ReAct steps (Thought -> Tool Result -> Final Answer)
        # where we want to "commit" the previous thought and start fresh.
        if getattr(msg, "new_message", False):
            if chat_id in self._message_buffers:
                # Flush and close existing buffer
                await self._flush_buffer(chat_id)
                del self._message_buffers[chat_id]
        
        if chat_id not in self._message_buffers:
            # First chunk: send initial message
            # We treat the first chunk as the initial content for its stream
            # initial_content = self._render_streams({stream_id: content})
            # message_id = await self._send_initial_message(chat_id, initial_content)
            
            streams = {stream_id: content}
            message_id = await self._send_initial_message(chat_id, streams=streams)
            
            if message_id:
                self._message_buffers[chat_id] = {
                    "message_id": message_id,
                    "streams": streams,
                    "dirty": False,  # Just sent, not dirty
                    "last_flush": time.time()
                }
        else:
            # Append to buffer
            buffer = self._message_buffers[chat_id]
            if "streams" not in buffer:
                 # Migration/Fallback if structure somehow wrong
                 buffer["streams"] = {"main": buffer.get("content", "")}
            
            if stream_id not in buffer["streams"]:
                buffer["streams"][stream_id] = ""
                
            buffer["streams"][stream_id] += content
            buffer["dirty"] = True

    async def _finalize_message(self, msg: OutboundMessage) -> None:
        """Finalize message (flush buffer and update with final content)."""
        chat_id = msg.chat_id
        
        # If new_message flag is set, force a new message regardless of buffer
        if getattr(msg, "new_message", False):
            if chat_id in self._message_buffers:
                # Flush old buffer first
                await self._flush_buffer(chat_id)
                del self._message_buffers[chat_id]
            # Send as new standalone message
            await self._send_initial_message(chat_id, content_text=msg.content)
            return

        if chat_id in self._message_buffers:
            # We have an ongoing stream
            buffer = self._message_buffers[chat_id]
            
            # Update main stream with final content (usually msg.content is full final response)
            # If msg.content is provided, it usually replaces the "main" stream or IS the final result.
            if msg.content:
                # We assume final message replaces "main" stream logic
                if "streams" not in buffer:
                    buffer["streams"] = {"main": ""}
                buffer["streams"]["main"] = msg.content
                buffer["dirty"] = True
            
            # Flush immediately
            await self._flush_buffer(chat_id)
            
            # Remove from buffer
            del self._message_buffers[chat_id]
        else:
            # No stream, just send normally
            await self._send_initial_message(chat_id, content_text=msg.content)

    def _render_card_elements(self, streams: dict[str, str]) -> list[dict]:
        """Render multiple streams into Feishu Card Elements (using column_set for subagents)."""
        elements = []
        
        # 1. Main stream
        main_content = streams.get("main", "").strip()
        if main_content:
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": self._preprocess_markdown(main_content)
                }
            })
            
        # 2. Sub-agent streams
        sorted_streams = sorted([k for k in streams.keys() if k != "main"])
        if sorted_streams:
            columns = []
            for s_id in sorted_streams:
                content = streams[s_id].strip()
                if content:
                    # Clean up content for display
                    # If it's a task log, extract the task name if possible or just use stream_id
                    display_title = s_id.replace("progress_", "Task: ") if s_id.startswith("progress_") else s_id
                    
                    columns.append({
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "elements": [
                            {
                                "tag": "div",
                                "text": {
                                    "tag": "lark_md",
                                    "content": self._preprocess_markdown(f"**🤖 {display_title}**\n{content}")
                                }
                            }
                        ]
                    })
            
            if columns:
                # Add a separator line if main content exists
                if elements:
                    elements.append({"tag": "hr"})
                    
                elements.append({
                    "tag": "column_set",
                    "flex_mode": "none",
                    "background_style": "grey",
                    "columns": columns
                })
        
        # Fallback if empty
        if not elements:
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "..."
                }
            })
            
        return elements

    async def _send_initial_message(self, chat_id: str, content_text: str = "", streams: Optional[dict[str, str]] = None) -> Optional[str]:
        """Send a new message (as a Card) and return its ID."""
        try:
            # Construct Card JSON
            if streams:
                elements = self._render_card_elements(streams)
            else:
                # Backward compatibility / simple text
                elements = [{
                    "tag": "div",
                    "text": {
                        "tag": "lark_md", 
                        "content": self._preprocess_markdown(content_text)
                    }
                }]
            
            card_content = {
                "config": {"wide_screen_mode": True},
                "elements": elements
            }
            content = json.dumps(card_content)
            
            # 根据 chat_id 前缀动态选择 receive_id_type
            # oc_ 开头是群聊 chat_id，否则是 open_id
            receive_id_type = "chat_id" if chat_id.startswith("oc_") else "open_id"

            request = CreateMessageRequest.builder() \
                .receive_id_type(receive_id_type) \
                .request_body(CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("interactive")  # Must be interactive for patching
                    .content(content)
                    .build()) \
                .build()
                
            response = await asyncio.to_thread(
                self._api_client.im.v1.message.create, request
            )
            
            if response.success():
                return response.data.message_id
            else:
                logger.error(f"Failed to create Feishu message: {response.code} - {response.msg}")
                return None
        except Exception as e:
            logger.error(f"Error creating Feishu message: {e}")
            return None

    async def _flush_loop(self) -> None:
        """Periodic flush loop."""
        while self._running:
            await asyncio.sleep(0.3)  # Flush every 300ms for smoother typing
            
            # Create a copy of keys to iterate
            chat_ids = list(self._message_buffers.keys())
            for chat_id in chat_ids:
                if chat_id in self._message_buffers:
                    await self._flush_buffer(chat_id)

    async def _flush_buffer(self, chat_id: str) -> None:
        """Flush buffer for a specific chat."""
        buffer = self._message_buffers.get(chat_id)
        if not buffer or not buffer["dirty"]:
            return
            
        try:
            # Render streams to Card Elements
            streams = buffer.get("streams", {"main": buffer.get("content", "")})
            elements = self._render_card_elements(streams)
            
            # Construct Card Update JSON
            card_content = {
                "config": {"wide_screen_mode": True},
                "elements": elements
            }
            content = json.dumps(card_content)
            
            request = PatchMessageRequest.builder() \
                .message_id(buffer["message_id"]) \
                .request_body(PatchMessageRequestBody.builder()
                    .content(content)
                    .build()) \
                .build()
            
            response = await asyncio.to_thread(
                self._api_client.im.v1.message.patch, request
            )
            
            if response.success():
                buffer["dirty"] = False
                buffer["last_flush"] = time.time()
            else:
                logger.warning(f"Failed to patch message: {response.code} - {response.msg}")
                # If patch fails (e.g. rate limit), keep dirty to retry next loop
                
        except Exception as e:
            logger.error(f"Error flushing buffer: {e}")

    def _fetch_parent_message_text(self, parent_message_id: str) -> Optional[str]:
        """Fetch the text content of a parent (quoted) message via Feishu API."""
        if not self._api_client:
            return None
        try:
            from lark_oapi.api.im.v1 import GetMessageRequest

            req = (
                GetMessageRequest.builder()
                .message_id(parent_message_id)
                .build()
            )
            resp = self._api_client.im.v1.message.get(req)
            if not resp.success():
                logger.warning(f"Feishu fetch parent message failed: {resp.code} - {resp.msg}")
                return None

            items = getattr(resp.data, "items", None)
            if not items:
                return None

            parent_msg = items[0]
            msg_type = getattr(parent_msg, "msg_type", "")
            body = getattr(parent_msg, "body", None)
            content_str = getattr(body, "content", "") if body else ""

            if not content_str:
                return None

            if msg_type == "text":
                return json.loads(content_str).get("text", "")
            elif msg_type == "post":
                parts = []
                for line in json.loads(content_str).get("content", []):
                    for elem in line:
                        if elem.get("tag") == "text":
                            parts.append(elem.get("text", ""))
                return "".join(parts)
            else:
                return f"[{msg_type} message]"
        except Exception as e:
            logger.warning(f"Failed to fetch parent message text: {e}")
            return None

    def _handle_im_message(self, data: P2ImMessageReceiveV1) -> None:
        """
        Handle incoming Feishu messages (Typed).
        Runs in the SDK's thread.
        """
        try:
            # Extract message details
            if not data.event or not data.event.message or not data.event.sender:
                logger.warning("Received incomplete Feishu message event")
                return

            message = data.event.message
            sender = data.event.sender
            
            open_id = sender.sender_id.open_id
            content_json = message.content
            msg_type = message.message_type
            message_id = message.message_id
            
            text = ""
            media_paths = []

            if msg_type == "text":
                try:
                    content_dict = json.loads(content_json)
                    text = content_dict.get("text", "")
                except json.JSONDecodeError:
                    text = content_json
            elif msg_type == "post":
                try:
                    content_dict = json.loads(content_json)
                    # Post content is like: {"content": [[{"tag": "text", "text": "..."}]]}
                    text = ""
                    for line in content_dict.get("content", []):
                        for elem in line:
                            if elem.get("tag") == "text":
                                text += elem.get("text", "")
                        text += "\n"
                except Exception as e:
                    logger.warning(f"Failed to parse post content: {e}")
                    return
            elif msg_type == "image":
                try:
                    image_key = json.loads(content_json).get("image_key", "")
                    if image_key:
                        path = self._download_message_resource(message_id, image_key, "image")
                        if path:
                            media_paths.append(path)
                            text = f"[image: {path}]"
                        else:
                            text = "[image: download failed]"
                    else:
                        text = "[image: no image_key]"
                except Exception as e:
                    logger.warning(f"Failed to handle image message: {e}")
                    text = "[image: download failed]"
            elif msg_type == "file":
                try:
                    content_dict = json.loads(content_json)
                    file_key = content_dict.get("file_key", "")
                    file_name = content_dict.get("file_name", "file")
                    if file_key:
                        path = self._download_message_resource(message_id, file_key, "file", file_name)
                        if path:
                            media_paths.append(path)
                            text = f"[file: {path}]"
                        else:
                            text = f"[file: download failed ({file_name})]"
                    else:
                        text = "[file: no file_key]"
                except Exception as e:
                    logger.warning(f"Failed to handle file message: {e}")
                    text = "[file: download failed]"
            elif msg_type == "audio":
                try:
                    file_key = json.loads(content_json).get("file_key", "")
                    if file_key:
                        path = self._download_message_resource(message_id, file_key, "audio")
                        if path:
                            media_paths.append(path)
                            text = f"[audio: {path}]"
                        else:
                            text = "[audio: download failed]"
                    else:
                        text = "[audio: no file_key]"
                except Exception as e:
                    logger.warning(f"Failed to handle audio message: {e}")
                    text = "[audio: download failed]"
            else:
                logger.debug(f"Ignoring unsupported message type: {msg_type}")
                return

            # Extract reply/quote context if this message replies to another
            parent_id = getattr(message, "parent_id", None)
            if parent_id:
                quoted_text = self._fetch_parent_message_text(parent_id)
                if quoted_text:
                    text = '[Replying to: "' + quoted_text[:200] + '"]\n' + text

            # Extract chat context for group/p2p routing
            chat_id = message.chat_id
            chat_type = message.chat_type  # "p2p" or "group"

            logger.info(f"Processing message from feishu ({chat_type}): {text[:50]}")

            # Schedule processing on the main event loop
            if self._main_loop and self._main_loop.is_running():
                # Use a safeguard against threadsafe call failures if loop is closing
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._process_message(open_id, text, message_id, chat_id, chat_type, media_paths),
                        self._main_loop
                    )
                except RuntimeError as e:
                    logger.error(f"Failed to schedule task on main loop (loop might be closed): {e}")
            else:
                logger.warning("Main loop not available to process Feishu message")
                
        except Exception as e:
            logger.error(f"Error handling Feishu event: {e}")

    async def _process_message(self, open_id: str, text: str, message_id: str,
                              chat_id: str = "", chat_type: str = "p2p",
                              media_paths: list = None) -> None:
        """Process the message on the main loop."""
        # Check allowed users if configured
        if self.config.allow_from and open_id not in self.config.allow_from:
            logger.warning(f"Message from unauthorized user: {open_id}")
            return

        is_group = chat_type == "group"
        # 群聊时回复到群，私聊时回复到个人
        reply_chat_id = chat_id if is_group else open_id

        await self._handle_message(
            sender_id=open_id,
            chat_id=reply_chat_id,
            content=text,
            media=media_paths or [],
            metadata={
                "message_id": message_id,
                "platform": "feishu",
                "chat_type": chat_type,
                "is_group": is_group,
            }
        )
