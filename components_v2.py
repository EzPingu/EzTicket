"""Discord Components V2 rendering helpers.

The configuration layer still stores the legacy values unchanged.  This module
only translates the presentation payload that was previously sent as an
``Embed`` into a Components V2 ``LayoutView``.
"""
from __future__ import annotations

from collections.abc import Iterable

import discord


def _field_text(field: discord.EmbedField) -> str:
    return f"**{field.name}**\n{field.value}"


def render_components_v2(
    embed: discord.Embed,
    view: discord.ui.View | discord.ui.LayoutView | None = None,
) -> discord.ui.LayoutView:
    """Render an existing embed payload as a Components V2 layout.

    Keeping the input as ``discord.Embed`` makes the migration presentation-only:
    all existing builders, branding, colours and persisted custom text continue
    to feed the renderer without changing the JSON schema.
    """
    if isinstance(view, discord.ui.LayoutView):
        return view

    children: list[discord.ui.Item] = []
    if embed.author and embed.author.name:
        author = embed.author.name
        if embed.author.icon_url:
            author = f"![{author}]({embed.author.icon_url}) {author}"
        children.append(discord.ui.TextDisplay(author))

    heading = embed.title or ""
    if heading:
        children.append(discord.ui.TextDisplay(f"# {heading}"))
    if embed.description:
        children.append(discord.ui.TextDisplay(embed.description))

    fields = list(embed.fields)
    if fields and children:
        children.append(discord.ui.Separator())
    for index, field in enumerate(fields):
        children.append(discord.ui.TextDisplay(_field_text(field)))
        if index != len(fields) - 1:
            children.append(discord.ui.Separator())

    if embed.thumbnail and embed.thumbnail.url:
        children.append(discord.ui.TextDisplay(f"![Immagine]({embed.thumbnail.url})"))

    footer_parts: list[str] = []
    if embed.footer and embed.footer.text:
        footer_parts.append(embed.footer.text)
    if embed.timestamp:
        footer_parts.append(f"<t:{int(embed.timestamp.timestamp())}:F>")
    if footer_parts:
        if children:
            children.append(discord.ui.Separator())
        children.append(discord.ui.TextDisplay(" · ".join(footer_parts)))

    if not children:
        children.append(discord.ui.TextDisplay("\u200b"))

    if view is not None:
        view_children: Iterable[discord.ui.Item] = tuple(view.children)
        if view_children:
            children.append(discord.ui.Separator())
            children.append(discord.ui.ActionRow(*view_children))

    layout = discord.ui.LayoutView(timeout=getattr(view, "timeout", None))
    colour = embed.color
    # Discord limits the number of children in a Container.  Splitting keeps
    # long help/configuration embeds valid without dropping any fields.
    for start in range(0, len(children), 10):
        layout.add_item(
            discord.ui.Container(
                *children[start:start + 10],
                accent_color=colour if colour is not None else None,
            )
        )
    return layout


def recolor_components_v2(view: discord.ui.LayoutView, colour: discord.Colour) -> discord.ui.LayoutView:
    """Update the accent colour of a reconstructed Components V2 message."""
    for item in view.children:
        if isinstance(item, discord.ui.Container):
            item.accent_color = colour
    return view
