"""Bridge unico tra il loop FastAPI e il loop gateway di discord.py."""
from __future__ import annotations

import asyncio
from typing import Awaitable, TypeVar

import discord

_client: discord.Client | None = None
_client_loop: asyncio.AbstractEventLoop | None = None
_T = TypeVar("_T")


def bind_client(client: discord.Client) -> None:
    global _client, _client_loop
    _client = client
    try:
        _client_loop = asyncio.get_running_loop()
    except RuntimeError:
        _client_loop = None


def get_guild(guild_id: int) -> discord.Guild | None:
    return _client.get_guild(int(guild_id)) if _client else None


def is_available() -> bool:
    return _client_loop is not None and _client_loop.is_running()


async def run_on_discord_loop(coro: Awaitable[_T]) -> _T:
    """Esegue `coro` sul loop dove è connesso il client Discord."""
    if _client_loop is None:
        close = getattr(coro, "close", None)
        if close:
            close()
        raise ValueError("bot_offline")
    current_loop = asyncio.get_running_loop()
    if current_loop is _client_loop:
        return await coro
    if not _client_loop.is_running():
        close = getattr(coro, "close", None)
        if close:
            close()
        raise ValueError("bot_offline")
    future = asyncio.run_coroutine_threadsafe(coro, _client_loop)
    try:
        return await asyncio.wrap_future(future)
    except asyncio.CancelledError:
        future.cancel()
        raise
