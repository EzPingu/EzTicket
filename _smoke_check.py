import discord
import main
from tickets import (
    ticket_group,
    TicketPanelView,
    TicketControlView,
    NotifyOptOutButton,
    NotifyOptInButton,
)
from candidature import candidatura_group, domandestaff_group
from settings import config_group

bot = main.bot
for g in (ticket_group, candidatura_group, domandestaff_group, config_group):
    bot.tree.add_command(g)
bot.add_view(TicketPanelView())
bot.add_view(TicketControlView())
bot.add_dynamic_items(NotifyOptOutButton, NotifyOptInButton)

cmds = bot.tree.get_commands()
print('TOP-LEVEL:', sorted(c.name for c in cmds))
for c in cmds:
    if isinstance(c, discord.app_commands.Group):
        sub = []
        for s in c.commands:
            if isinstance(s, discord.app_commands.Group):
                sub.append(s.name + '{' + ','.join(x.name for x in s.commands) + '}')
            else:
                sub.append(s.name)
        print('  ', c.name, '->', sorted(sub))

payload = bot.tree._get_all_commands(guild=None)
print('PAYLOAD OK, comandi globali:', len(payload))
for c in payload:
    c.to_dict(bot.tree)
print('to_dict OK')

# I template dei DynamicItem devono combaciare con i custom_id realmente emessi
import re
for cls, cid in (
    (NotifyOptOutButton, 'tnotify:out:123456789'),
    (NotifyOptInButton, 'tnotify:in:123456789'),
):
    m = cls.__discord_ui_compiled_template__.fullmatch(cid)
    assert m and m['gid'] == '123456789', (cls.__name__, cid)
print('DYNAMIC ITEM TEMPLATES OK')

view = TicketPanelView()
print('panel custom_ids:', [i.custom_id for i in view.children])
print('control custom_ids:', [i.custom_id for i in TicketControlView().children])
