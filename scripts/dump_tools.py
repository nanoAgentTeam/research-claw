import asyncio
import os
import sys
from pathlib import Path
from loguru import logger

# Add current directory to path
sys.path.append(os.getcwd())

from agent.loop import AgentLoop
from bus.queue import MessageBus

async def dump_tools():
    bus = MessageBus()
    # Mock provider
    class MockProvider:
        def __init__(self):
            self.api_key = "test"
            self.api_base = "test"

    workspace = Path(os.getcwd())
    agent = AgentLoop(bus=bus, provider=MockProvider(), workspace=workspace, model="test")

    tools = agent.tools.get_definitions()
    import json
    print(json.dumps(tools, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    asyncio.run(dump_tools())
