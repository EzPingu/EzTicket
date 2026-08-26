"""
manager_backend/services/guild_service.py
Service per l'interazione sicura con EzTicket Core (ConfigManager) e la risoluzione dei permessi per-guild.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from config import cfg, is_admin_member, is_bot_operator, is_staff_member
from manager_backend.models.schemas import GuildDetailResponse, GuildSummaryResponse
from tickets import _get_guild

log = logging.getLogger("ezticket.manager.guild_service")

# Maschere permessi Discord standard
DISCORD_PERM_ADMINISTRATOR = 0x8
DISCORD_PERM_MANAGE_GUILD = 0x20


@dataclass
class GuildAccessInfo:
    guild_id: str
    is_present: bool
    is_authorized: bool
    role: str  # "GUILD_ADMIN" | "GUILD_STAFF" | "NONE"
    is_owner: bool = False
    is_admin: bool = False
    guild_config: Optional[dict[str, Any]] = None


class GuildService:
    """Service layer centralizzato per l'autorizzazione e le informazioni di contesto per-server."""

    def is_bot_in_guild(self, guild_id: str | int) -> bool:
        """Verifica se il bot è presente e configurato per la guild richiesta."""
        gconf = cfg.peek(guild_id)
        if gconf is None:
            return False
        return gconf.get("left_at") is None

    def evaluate_guild_access(
        self,
        user_id: int,
        guild_id: str | int,
        discord_guilds: Optional[list[dict[str, Any]]] = None,
    ) -> GuildAccessInfo:
        """Valuta in modo rigoroso e server-side l'accesso di un utente a una specifica guild.

        Non si fida di input esterni non convalidati.
        """
        gid_str = str(guild_id)
        gconf = cfg.peek(gid_str)

        # 1. Bot non presente o server abbandonato
        if gconf is None or gconf.get("left_at") is not None:
            return GuildAccessInfo(
                guild_id=gid_str,
                is_present=False,
                is_authorized=False,
                role="NONE",
            )

        # 2. Controllo Owner osservato o registrato in EzTicket
        observed_owner = gconf.get("observed_owner_id")
        is_owner = observed_owner is not None and observed_owner == user_id

        # 3. Controllo Admin espliciti (/config owner add)
        admin_ids = cfg.admin_ids(gid_str)
        is_explicit_admin = user_id in admin_ids

        # 4. Controllo permessi Discord da lista OAuth (se disponibile)
        has_discord_admin_perm = False
        if discord_guilds:
            for dg in discord_guilds:
                if str(dg.get("id")) == gid_str:
                    if dg.get("owner") is True:
                        is_owner = True
                    perms_raw = dg.get("permissions")
                    if perms_raw is not None:
                        try:
                            perms_int = int(perms_raw)
                            if perms_int & DISCORD_PERM_ADMINISTRATOR:
                                has_discord_admin_perm = True
                        except (ValueError, TypeError):
                            pass
                    break

        # 5. Controllo con live Discord member se client attivo
        live_guild = _get_guild(int(gid_str)) if gid_str.isdigit() else None
        if live_guild:
            member = live_guild.get_member(user_id)
            if member:
                if is_admin_member(member, int(gid_str)):
                    return GuildAccessInfo(
                        guild_id=gid_str,
                        is_present=True,
                        is_authorized=True,
                        role="GUILD_ADMIN",
                        is_owner=is_owner or (live_guild.owner_id == user_id),
                        is_admin=True,
                        guild_config=gconf,
                    )
                if is_staff_member(member, int(gid_str)):
                    return GuildAccessInfo(
                        guild_id=gid_str,
                        is_present=True,
                        is_authorized=True,
                        role="GUILD_STAFF",
                        is_owner=False,
                        is_admin=False,
                        guild_config=gconf,
                    )

        # Risoluzione ruolo Admin
        if is_owner or is_explicit_admin or has_discord_admin_perm:
            return GuildAccessInfo(
                guild_id=gid_str,
                is_present=True,
                is_authorized=True,
                role="GUILD_ADMIN",
                is_owner=is_owner,
                is_admin=True,
                guild_config=gconf,
            )

        # In assenza di ruoli admin o staff verificati, l'accesso è negato
        return GuildAccessInfo(
            guild_id=gid_str,
            is_present=True,
            is_authorized=False,
            role="NONE",
            is_owner=False,
            is_admin=False,
            guild_config=gconf,
        )

    def get_accessible_guilds(
        self,
        user_id: int,
        discord_guilds: Optional[list[dict[str, Any]]] = None,
    ) -> list[GuildSummaryResponse]:
        """Restituisce SOLO le guild in cui il bot è presente E l'utente è autorizzato."""
        results: list[GuildSummaryResponse] = []
        discord_guild_map = {str(g.get("id")): g for g in (discord_guilds or [])}

        # Itera su tutte le guild conosciute dal ConfigManager
        for gid_str, gconf in cfg.data.items():
            if gconf.get("left_at") is not None:
                continue

            access = self.evaluate_guild_access(user_id, gid_str, discord_guilds)
            if not access.is_authorized:
                continue

            dg_meta = discord_guild_map.get(gid_str, {})
            guild_name = dg_meta.get("name") or gconf.get("branding") or f"Guild {gid_str}"
            guild_icon = dg_meta.get("icon")

            active_tickets = len(gconf.get("tickets") or {})

            results.append(
                GuildSummaryResponse(
                    id=gid_str,
                    name=guild_name,
                    icon=guild_icon,
                    role=access.role,
                    is_owner=access.is_owner,
                    is_admin=access.is_admin,
                    active_tickets_count=active_tickets,
                )
            )
        return results

    def get_guild_detail(
        self,
        user_id: int,
        guild_id: str | int,
        discord_guilds: Optional[list[dict[str, Any]]] = None,
    ) -> Optional[GuildDetailResponse]:
        """Restituisce i dettagli autorizzati di una guild, o None se non autorizzato."""
        access = self.evaluate_guild_access(user_id, guild_id, discord_guilds)
        if not access.is_present or not access.is_authorized or access.guild_config is None:
            return None

        gconf = access.guild_config
        gid_str = str(guild_id)
        discord_guild_map = {str(g.get("id")): g for g in (discord_guilds or [])}
        dg_meta = discord_guild_map.get(gid_str, {})

        return GuildDetailResponse(
            id=gid_str,
            name=dg_meta.get("name") or gconf.get("branding") or f"Guild {gid_str}",
            icon=dg_meta.get("icon"),
            user_role=access.role,
            is_owner=access.is_owner,
            is_admin=access.is_admin,
            sections_count=len(gconf.get("sections") or {}),
            branding=gconf.get("branding"),
            sla_seconds=int(gconf.get("sla_seconds") or 300),
            claim_timeout_seconds=int(gconf.get("claim_timeout_seconds") or 120),
            inactivity_hours=int(gconf.get("inactivity_hours") or 24),
        )


guild_service = GuildService()
