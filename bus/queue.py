"""Async message queue for decoupled channel-agent communication."""

import asyncio
from typing import Callable, Awaitable

from loguru import logger

from bus.events import InboundMessage, OutboundMessage


class MessageBus:
    """
    Async message bus that decouples chat channels from the agent core.

    Channels push messages to the inbound queue, and the agent processes
    them and pushes responses to the outbound queue.
    """

    def __init__(self):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()
        self._outbound_subscribers: dict[str, list[Callable[[OutboundMessage], Awaitable[None]]]] = {}
        self._inbound_hooks: list[Callable[[InboundMessage], Awaitable[None]]] = []
        self._outbound_hooks: list[Callable[[OutboundMessage], Awaitable[None]]] = []
        self._running = False

    def add_inbound_hook(self, callback: Callable[[InboundMessage], Awaitable[None]]) -> None:
        """Register a hook called on every inbound message (e.g. history logging)."""
        self._inbound_hooks.append(callback)

    def add_outbound_hook(self, callback: Callable[[OutboundMessage], Awaitable[None]]) -> None:
        """Register a hook called on every outbound message (e.g. history logging)."""
        self._outbound_hooks.append(callback)

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish a message from a channel to the agent."""
        for hook in self._inbound_hooks:
            try:
                await hook(msg)
            except Exception as e:
                logger.debug(f"Inbound hook error: {e}")
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message (blocks until available)."""
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish a response from the agent to channels."""
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message (blocks until available)."""
        return await self.outbound.get()

    def subscribe_outbound(
        self,
        channel: str,
        callback: Callable[[OutboundMessage], Awaitable[None]]
    ) -> None:
        """Subscribe to outbound messages for a specific channel."""
        if channel not in self._outbound_subscribers:
            self._outbound_subscribers[channel] = []
        # Prevent duplicate subscribers — replace existing list with single callback
        # This guards against hot-swap scenarios where old callback references linger
        if callback not in self._outbound_subscribers[channel]:
            self._outbound_subscribers[channel].append(callback)

    def unsubscribe_outbound(
        self,
        channel: str,
        callback: Callable[[OutboundMessage], Awaitable[None]]
    ) -> None:
        """Remove a previously registered outbound subscriber."""
        if channel in self._outbound_subscribers:
            try:
                self._outbound_subscribers[channel].remove(callback)
            except ValueError:
                pass

    async def dispatch_outbound(self) -> None:
        """
        Dispatch outbound messages to subscribed channels.
        Run this as a background task.
        """
        self._running = True
        while self._running:
            try:
                msg = await asyncio.wait_for(self.outbound.get(), timeout=1.0)
                for hook in self._outbound_hooks:
                    try:
                        await hook(msg)
                    except Exception as e:
                        logger.debug(f"Outbound hook error: {e}")
                subscribers = self._outbound_subscribers.get(msg.channel, [])
                for callback in subscribers:
                    try:
                        await callback(msg)
                    except Exception as e:
                        logger.error(f"Error dispatching to {msg.channel}: {e}")
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        """Stop the dispatcher loop."""
        self._running = False

    @property
    def inbound_size(self) -> int:
        """Number of pending inbound messages."""
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        """Number of pending outbound messages."""
        return self.outbound.qsize()
