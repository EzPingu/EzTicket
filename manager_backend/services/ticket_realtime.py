"""Distribuzione in tempo reale dei nuovi messaggi dei ticket."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any


class TicketRealtime:
    def __init__(self) -> None:
        self._subscribers: dict[tuple[str, str], set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]]]]] = defaultdict(set)

    def subscribe(self, guild_id: str, channel_id: str) -> tuple[asyncio.Queue[dict[str, Any]], tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]]]]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        subscriber = (loop, queue)
        self._subscribers[(str(guild_id), str(channel_id))].add(subscriber)
        return queue, subscriber

    def unsubscribe(self, guild_id: str, channel_id: str, subscriber: tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]]]) -> None:
        subscribers = self._subscribers.get((str(guild_id), str(channel_id)))
        if subscribers is None:
            return
        subscribers.discard(subscriber)
        if not subscribers:
            self._subscribers.pop((str(guild_id), str(channel_id)), None)

    def publish(self, guild_id: str | int, channel_id: str | int, message: dict[str, Any]) -> None:
        for loop, queue in tuple(self._subscribers.get((str(guild_id), str(channel_id)), ())):
            loop.call_soon_threadsafe(self._enqueue, queue, message)

    @staticmethod
    def _enqueue(queue: asyncio.Queue[dict[str, Any]], message: dict[str, Any]) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait(message)


ticket_realtime = TicketRealtime()
