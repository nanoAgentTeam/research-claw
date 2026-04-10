import asyncio
import json
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.append(str(Path(__file__).parent.parent))

from config.loader import load_config
from bus.queue import MessageBus
from bus.events import InboundMessage, OutboundMessage
from agent.loop import AgentLoop
from providers.openai_provider import OpenAIProvider
from channels.feishu import FeishuChannel

async def simulate():
    print("🚀 Starting Feishu Simulation...")

    config = load_config()
    bus = MessageBus()

    api_key = config.get_api_key()
    api_base = config.get_api_base()
    model_name = config.get_api_model() or config.agents.defaults.model

    if not api_key:
        from config.loader import get_config_path
        print(f"❌ Error: No API key configured in {get_config_path()}")
        return

    provider = OpenAIProvider(
        api_key=api_key,
        api_base=api_base,
        default_model=model_name
    )

    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=model_name,
    )

    # We use a mock Feishu channel that just prints the cards it would send
    class MockFeishuChannel(FeishuChannel):
        async def _send_initial_message(self, chat_id, content_text="", streams=None):
            print(f"\n[Feishu Card (Initial)] ──────────")
            if streams:
                elements = self._render_card_elements(streams)
                print(json.dumps(elements, indent=2, ensure_ascii=False))
            else:
                print(f"Content: {content_text}")
            print(f"─────────────────────────────────\n")
            return "mock_msg_123"

        async def _flush_buffer(self, chat_id):
            buffer = self._message_buffers.get(chat_id)
            if not buffer or not buffer["dirty"]:
                return

            streams = buffer.get("streams", {"main": buffer.get("content", "")})
            elements = self._render_card_elements(streams)
            print(f"\n[Feishu Card (Update)] ──────────")
            print(json.dumps(elements, indent=2, ensure_ascii=False))
            print(f"─────────────────────────────────\n")
            buffer["dirty"] = False

    feishu = MockFeishuChannel(config.channels.feishu, bus)

    # Start tasks
    tasks = [
        asyncio.create_task(agent.run()),
        asyncio.create_task(bus.dispatch_outbound()),
    ]

    # Subscribe mock channel
    bus.subscribe_outbound("feishu", feishu.send)

    print("🤖 Simulation ready. Type your message below:")

    try:
        while True:
            text = await asyncio.to_thread(input, "You: ")
            if not text.strip():
                continue

            # Simulate an inbound message from Feishu
            await bus.publish_inbound(InboundMessage(
                channel="feishu",
                sender_id="user_123",
                chat_id="chat_123",
                content=text,
                metadata={"platform": "feishu"}
            ))

            # Wait a bit for processing
            await asyncio.sleep(0.5)

    except KeyboardInterrupt:
        print("\nStopping simulation...")
    finally:
        for t in tasks:
            t.cancel()

if __name__ == "__main__":
    asyncio.run(simulate())
