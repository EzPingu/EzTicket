"""
manager_backend/services/history_service.py
Service per la consultazione e la ricerca nello storico dei ticket chiusi per-guild.
Include paginazione, filtri su data, opener, staff, sezione, ricerca testuale e ordinamento.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Optional

from config import cfg
from manager_backend.models.schemas import (
    PaginatedTicketHistoryResponse,
    TicketHistoryDetailResponse,
    TicketHistoryItemResponse,
)
from tickets import _get_guild

log = logging.getLogger("ezticket.manager.history_service")


class HistoryService:
    """Service layer per lo storico dei ticket chiusi."""

    def get_history_paginated(
        self,
        guild_id: str | int,
        page: int = 1,
        page_size: int = 20,
        start_date: Optional[int] = None,
        end_date: Optional[int] = None,
        opener_id: Optional[str] = None,
        staff_id: Optional[str] = None,
        section: Optional[str] = None,
        search: Optional[str] = None,
        sort_by: str = "closed_at",
        order: str = "desc",
    ) -> PaginatedTicketHistoryResponse:
        gid_str = str(guild_id)
        gconf = cfg.peek(gid_str)
        if gconf is None or gconf.get("left_at") is not None:
            return PaginatedTicketHistoryResponse(
                guild_id=gid_str,
                items=[],
                total=0,
                page=page,
                page_size=page_size,
                total_pages=0,
            )

        raw_history = cfg.guild_history(gid_str)
        sections = gconf.get("sections") or {}
        live_guild = _get_guild(int(gid_str)) if gid_str.isdigit() else None

        filtered: list[dict[str, Any]] = []
        search_lower = search.lower().strip() if search else None

        for entry in raw_history:
            if not isinstance(entry, dict):
                continue

            entry_opened = entry.get("opened_at")
            entry_closed = entry.get("closed_at")
            entry_date = entry_closed if entry_closed is not None else entry_opened

            # 1. Filtro data inizio
            if start_date is not None:
                if entry_date is None or entry_date < start_date:
                    continue

            # 2. Filtro data fine
            if end_date is not None:
                if entry_date is None or entry_date > end_date:
                    continue

            # 3. Filtro opener
            entry_opener = str(entry.get("opener") or "")
            if opener_id is not None:
                if entry_opener != str(opener_id):
                    continue

            # 4. Filtro staff (claimed_by o closed_by)
            entry_claimed = str(entry.get("claimed_by") or "") if entry.get("claimed_by") else None
            entry_closed_by = str(entry.get("closed_by") or "") if entry.get("closed_by") else None
            if staff_id is not None:
                staff_str = str(staff_id)
                if entry_claimed != staff_str and entry_closed_by != staff_str:
                    continue

            # 5. Filtro sezione
            entry_sec = str(entry.get("section") or "")
            if section is not None:
                if entry_sec != str(section):
                    continue

            # 6. Ricerca testuale semplice
            if search_lower:
                num_str = str(entry.get("number") or "")
                ch_name = str(entry.get("channel_name") or "").lower()
                motivo_str = str(entry.get("motivo") or "").lower()
                reason_str = str(entry.get("close_reason") or "").lower()
                opener_str = entry_opener.lower()

                matched = (
                    search_lower in num_str
                    or search_lower in ch_name
                    or search_lower in motivo_str
                    or search_lower in reason_str
                    or search_lower in opener_str
                )
                if not matched:
                    continue

            filtered.append(entry)

        # 7. Ordinamento
        valid_sort_fields = {"closed_at", "opened_at", "number", "duration_seconds"}
        actual_sort_field = sort_by if sort_by in valid_sort_fields else "closed_at"
        is_reverse = (order.lower() != "asc")

        def sort_key(e: dict[str, Any]):
            val = e.get(actual_sort_field)
            if val is None:
                return -1 if is_reverse else float("inf")
            return val

        filtered.sort(key=sort_key, reverse=is_reverse)

        # 8. Paginazione
        total = len(filtered)
        total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 0
        current_page = max(1, min(page, total_pages)) if total_pages > 0 else 1

        start_idx = (current_page - 1) * page_size
        end_idx = start_idx + page_size
        page_entries = filtered[start_idx:end_idx]

        items: list[TicketHistoryItemResponse] = []
        for entry in page_entries:
            sec_key = str(entry.get("section") or "")
            sec_meta = sections.get(sec_key, {})

            op_id = entry.get("opener")
            cl_by_id = entry.get("closed_by")
            op_name: Optional[str] = None
            cl_name: Optional[str] = None
            op_avatar: Optional[str] = None
            cl_avatar: Optional[str] = None

            if live_guild:
                if op_id:
                    m = live_guild.get_member(int(op_id))
                    if m:
                        op_name = m.display_name
                        op_avatar = str(m.display_avatar.url)
                if cl_by_id:
                    m = live_guild.get_member(int(cl_by_id))
                    if m:
                        cl_name = m.display_name
                        cl_avatar = str(m.display_avatar.url)

            notes = entry.get("notes") or []
            notes_count = len(notes) if isinstance(notes, list) else 0
            transcript_url = entry.get("transcript_url")
            if not transcript_url and entry.get("transcript_sent") and gconf.get("transcript_channel"):
                transcript_url = f"https://discord.com/channels/{gid_str}/{gconf['transcript_channel']}"

            items.append(
                TicketHistoryItemResponse(
                    guild_id=gid_str,
                    number=int(entry.get("number") or 0),
                    channel_name=str(entry.get("channel_name") or ""),
                    section=sec_key,
                    section_label=sec_meta.get("label"),
                    section_emoji=sec_meta.get("emoji"),
                    motivo=entry.get("motivo"),
                    opener_id=str(op_id) if op_id else "0",
                    opener_name=op_name,
                    opener_avatar=op_avatar,
                    claimed_by=str(entry.get("claimed_by")) if entry.get("claimed_by") else None,
                    closed_by=str(cl_by_id) if cl_by_id else None,
                    closed_by_name=cl_name,
                    closed_by_avatar=cl_avatar,
                    close_reason=entry.get("close_reason"),
                    opened_at=entry.get("opened_at"),
                    closed_at=entry.get("closed_at"),
                    duration_seconds=entry.get("duration_seconds"),
                    transcript_sent=bool(entry.get("transcript_sent", False)),
                    transcript_url=transcript_url,
                    notes_count=notes_count,
                    channel_deleted=bool(entry.get("channel_deleted", False)),
                )
            )

        return PaginatedTicketHistoryResponse(
            guild_id=gid_str,
            items=items,
            total=total,
            page=current_page,
            page_size=page_size,
            total_pages=total_pages,
        )

    def get_history_detail(
        self,
        guild_id: str | int,
        ticket_id_or_number: str,
    ) -> Optional[TicketHistoryDetailResponse]:
        gid_str = str(guild_id)
        gconf = cfg.peek(gid_str)
        if gconf is None or gconf.get("left_at") is not None:
            return None

        raw_history = cfg.guild_history(gid_str)
        sections = gconf.get("sections") or {}
        live_guild = _get_guild(int(gid_str)) if gid_str.isdigit() else None

        target = ticket_id_or_number.strip().lstrip("#")
        target_num = int(target) if target.isdigit() else None

        found_entry: Optional[dict[str, Any]] = None
        for entry in reversed(raw_history):
            if not isinstance(entry, dict):
                continue
            entry_num = entry.get("number")
            entry_ch_name = str(entry.get("channel_name") or "")

            if target_num is not None and entry_num == target_num:
                found_entry = entry
                break
            if entry_ch_name == ticket_id_or_number or entry_ch_name == target:
                found_entry = entry
                break

        if found_entry is None:
            return None

        sec_key = str(found_entry.get("section") or "")
        sec_meta = sections.get(sec_key, {})

        op_id = found_entry.get("opener")
        cl_by_id = found_entry.get("closed_by")
        op_name: Optional[str] = None
        cl_name: Optional[str] = None
        op_avatar: Optional[str] = None
        cl_avatar: Optional[str] = None

        if live_guild:
            if op_id:
                m = live_guild.get_member(int(op_id))
                if m:
                    op_name = m.display_name
                    op_avatar = str(m.display_avatar.url)
            if cl_by_id:
                m = live_guild.get_member(int(cl_by_id))
                if m:
                    cl_name = m.display_name
                    cl_avatar = str(m.display_avatar.url)

        notes = [n for n in (found_entry.get("notes") or []) if isinstance(n, dict)]
        added_members = [str(m) for m in (found_entry.get("added_members") or [])]
        transcript_url = found_entry.get("transcript_url")
        if not transcript_url and found_entry.get("transcript_sent") and gconf.get("transcript_channel"):
            transcript_url = f"https://discord.com/channels/{gid_str}/{gconf['transcript_channel']}"
        return TicketHistoryDetailResponse(
            guild_id=gid_str,
            number=int(found_entry.get("number") or 0),
            channel_name=str(found_entry.get("channel_name") or ""),
            section=sec_key,
            section_label=sec_meta.get("label"),
            section_emoji=sec_meta.get("emoji"),
            motivo=found_entry.get("motivo"),
            opener_id=str(op_id) if op_id else "0",
            opener_name=op_name,
            opener_avatar=op_avatar,
            claimed_by=str(found_entry.get("claimed_by")) if found_entry.get("claimed_by") else None,
            closed_by=str(cl_by_id) if cl_by_id else None,
            closed_by_name=cl_name,
            closed_by_avatar=cl_avatar,
            close_reason=found_entry.get("close_reason"),
            opened_at=found_entry.get("opened_at"),
            closed_at=found_entry.get("closed_at"),
            duration_seconds=found_entry.get("duration_seconds"),
            transcript_sent=bool(found_entry.get("transcript_sent", False)),
            transcript_url=transcript_url,
            notes_count=len(notes),
            channel_deleted=bool(found_entry.get("channel_deleted", False)),
            added_members=added_members,
            notes=notes,
        )


history_service = HistoryService()
