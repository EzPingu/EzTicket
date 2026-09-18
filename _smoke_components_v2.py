"""Smoke test for legacy JSON preservation and Components V2 rendering."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import discord

from components_v2 import render_components_v2


def main() -> None:
    path = Path(__file__).with_name("config_ticket.json")
    legacy = json.loads(path.read_text(encoding="utf-8"))
    snapshot = copy.deepcopy(legacy)
    assert isinstance(legacy, dict) and legacy, "config_ticket.json must contain guild data"

    guild_config = next(iter(legacy.values()))
    panel = guild_config.get("panel") or {}
    embed = discord.Embed(
        title=panel.get("title") or "Centro Assistenza",
        description=panel.get("description") or "Seleziona una categoria",
        color=discord.Color.blurple(),
    )
    rendered = render_components_v2(embed)

    assert rendered.to_components(), "Components V2 renderer returned no components"
    assert legacy == snapshot, "Renderer must not mutate the legacy JSON payload"
    assert "branding" in guild_config, "Legacy branding setting must remain available"
    assert "sections" in guild_config, "Legacy ticket sections must remain available"
    assert "panel" in guild_config, "Legacy panel settings must remain available"
    print("Components V2 legacy JSON compatibility: OK")


if __name__ == "__main__":
    main()
