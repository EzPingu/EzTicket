"""
manager_backend/services/stats_service.py
Service per la generazione delle metriche Dashboard e Statistiche dettagliate per-guild.
Include elaborazione dati sicura, metriche affidabili, in-memory caching leggero (zero Redis)
e gestione trasparente dei campi non disponibili nel modello di storage corrente.
"""
from __future__ import annotations

import logging
import statistics
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from config import cfg, sla_seconds
from manager_backend.models.schemas import (
    ApplicationsStatistics,
    DailyTicketCount,
    DashboardSummaryResponse,
    GuildStatisticsResponse,
    MonthlyTicketCount,
    ResolutionTimeStats,
    SectionStats,
    SlaStatistics,
    StaffActivityStats,
    WeeklyTicketCount,
)
from manager_backend.services.ticket_service import ticket_service
from tickets import _get_guild

log = logging.getLogger("ezticket.manager.stats_service")


class StatsService:
    """Service layer per statistiche e metriche di dashboard."""

    def __init__(self, cache_ttl_seconds: int = 15):
        self._cache_ttl = cache_ttl_seconds
        # guild_id -> {"dashboard": (timestamp, data), "stats": (timestamp, data)}
        self._cache: dict[str, dict[str, tuple[float, Any]]] = defaultdict(dict)

    def invalidate_cache(self, guild_id: Optional[str | int] = None) -> None:
        """Invalida la cache per una guild specifica o per tutte le guild."""
        if guild_id is not None:
            self._cache.pop(str(guild_id), None)
        else:
            self._cache.clear()

    def get_dashboard_summary(
        self,
        guild_id: str | int,
        force_refresh: bool = False,
    ) -> DashboardSummaryResponse:
        gid_str = str(guild_id)
        now_ts = time.time()

        if not force_refresh:
            cached = self._cache.get(gid_str, {}).get("dashboard")
            if cached:
                cached_time, cached_data = cached
                if (now_ts - cached_time) < self._cache_ttl:
                    return cached_data

        gconf = cfg.peek(gid_str)
        if gconf is None or gconf.get("left_at") is not None:
            empty_resp = DashboardSummaryResponse(
                guild_id=gid_str,
                active_tickets=0,
                closed_tickets_total=0,
                pending_applications=0,
                tickets_today=0,
                tickets_this_week=0,
                tickets_this_month=0,
                average_resolution_time_seconds=None,
                average_first_response_time_seconds=None,
                sla_compliance_rate=None,
                tickets_by_section={},
                tickets_by_status={"open": 0, "claimed": 0, "unclaimed": 0},
                sla_summary={"breached": 0, "warning": 0, "pending": 0, "responded": 0},
                data_freshness_timestamp=int(now_ts),
            )
            return empty_resp

        raw_active = gconf.get("tickets") or {}
        raw_history = cfg.guild_history(gid_str)
        raw_apps = gconf.get("candidature_summary_messages") or {}
        sections = gconf.get("sections") or {}

        # 1. Conteggi base
        active_count = len(raw_active)
        closed_count = len(raw_history)
        pending_apps_count = len(raw_apps)

        # 2. Finestre temporali UTC
        now_utc = datetime.now(timezone.utc)
        today_start = int(now_utc.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        week_start = int((now_utc - timedelta(days=now_utc.weekday())).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        month_start = int(now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())

        tickets_today = 0
        tickets_this_week = 0
        tickets_this_month = 0

        # Raggruppamento per sezione e stato ticket attivi
        tickets_by_section: dict[str, int] = defaultdict(int)
        claimed_active = 0
        unclaimed_active = 0

        # First response times (calcolati su ticket attivi con prima risposta)
        first_resp_durations: list[float] = []

        # SLA status conteggi
        sla_counts = {"breached": 0, "warning": 0, "pending": 0, "responded": 0}

        for ticket in raw_active.values():
            if not isinstance(ticket, dict):
                continue
            opened_at = ticket.get("opened_at")
            if opened_at and isinstance(opened_at, (int, float)):
                if opened_at >= today_start:
                    tickets_today += 1
                if opened_at >= week_start:
                    tickets_this_week += 1
                if opened_at >= month_start:
                    tickets_this_month += 1

            sec_key = str(ticket.get("section") or "unspecified")
            tickets_by_section[sec_key] += 1

            if ticket.get("claimed_by"):
                claimed_active += 1
            else:
                unclaimed_active += 1

            sla_st = ticket_service.compute_sla_status(ticket)
            if sla_st in sla_counts:
                sla_counts[sla_st] += 1

            first_resp = ticket.get("first_response_at")
            if first_resp and opened_at and isinstance(first_resp, (int, float)) and isinstance(opened_at, (int, float)):
                diff = first_resp - opened_at
                if diff >= 0:
                    first_resp_durations.append(float(diff))

        # Analisi storico per finestre temporali e tempi di risoluzione
        resolution_durations: list[float] = []
        for entry in raw_history:
            if not isinstance(entry, dict):
                continue
            opened_at = entry.get("opened_at")
            if opened_at and isinstance(opened_at, (int, float)):
                if opened_at >= today_start:
                    tickets_today += 1
                if opened_at >= week_start:
                    tickets_this_week += 1
                if opened_at >= month_start:
                    tickets_this_month += 1

            duration = entry.get("duration_seconds")
            if duration is None:
                closed_at = entry.get("closed_at")
                if opened_at and closed_at and isinstance(closed_at, (int, float)) and isinstance(opened_at, (int, float)):
                    duration = closed_at - opened_at

            if duration is not None and isinstance(duration, (int, float)) and duration >= 0:
                resolution_durations.append(float(duration))

        # 3. Medie
        avg_resolution: Optional[float] = (
            round(sum(resolution_durations) / len(resolution_durations), 1)
            if resolution_durations
            else None
        )
        avg_first_resp: Optional[float] = (
            round(sum(first_resp_durations) / len(first_resp_durations), 1)
            if first_resp_durations
            else None
        )

        # 4. Compliance SLA
        total_sla_evaluated = sum(sla_counts.values())
        if total_sla_evaluated > 0:
            breached = sla_counts["breached"]
            responded = sla_counts["responded"]
            if (responded + breached) > 0:
                sla_compliance = round((responded / (responded + breached)) * 100.0, 1)
            else:
                sla_compliance = 100.0
        else:
            sla_compliance = None

        response = DashboardSummaryResponse(
            guild_id=gid_str,
            active_tickets=active_count,
            closed_tickets_total=closed_count,
            pending_applications=pending_apps_count,
            tickets_today=tickets_today,
            tickets_this_week=tickets_this_week,
            tickets_this_month=tickets_this_month,
            average_resolution_time_seconds=avg_resolution,
            average_first_response_time_seconds=avg_first_resp,
            sla_compliance_rate=sla_compliance,
            tickets_by_section=dict(tickets_by_section),
            tickets_by_status={
                "open": active_count,
                "claimed": claimed_active,
                "unclaimed": unclaimed_active,
            },
            sla_summary=sla_counts,
            data_freshness_timestamp=int(now_ts),
        )

        self._cache[gid_str]["dashboard"] = (now_ts, response)
        return response

    def get_guild_statistics(
        self,
        guild_id: str | int,
        days: int = 30,
        force_refresh: bool = False,
    ) -> GuildStatisticsResponse:
        gid_str = str(guild_id)
        now_ts = time.time()
        days_bounded = max(1, min(days, 365))

        if not force_refresh:
            cached = self._cache.get(gid_str, {}).get(f"stats_{days_bounded}")
            if cached:
                cached_time, cached_data = cached
                if (now_ts - cached_time) < self._cache_ttl:
                    return cached_data

        gconf = cfg.peek(gid_str)
        if gconf is None or gconf.get("left_at") is not None:
            empty_stats = GuildStatisticsResponse(
                guild_id=gid_str,
                timeframe_days=days_bounded,
                tickets_daily=[],
                tickets_weekly=[],
                tickets_monthly=[],
                tickets_by_section=[],
                tickets_by_staff=[],
                resolution_time=ResolutionTimeStats(),
                sla=SlaStatistics(
                    sla_target_seconds=sla_seconds(gid_str),
                    active_breached=0,
                    active_warning=0,
                    active_pending=0,
                    active_responded=0,
                    total_active_evaluated=0,
                    compliance_rate=None,
                ),
                applications=ApplicationsStatistics(
                    pending_review=0,
                    in_progress_dm=0,
                    historical_processed_total=None,
                    historical_data_available=False,
                ),
                data_freshness_timestamp=int(now_ts),
            )
            return empty_stats

        raw_active = gconf.get("tickets") or {}
        raw_history = cfg.guild_history(gid_str)
        sections = gconf.get("sections") or {}
        live_guild = _get_guild(int(gid_str)) if gid_str.isdigit() else None

        # 1. Timeline giornaliera (ultimi N giorni)
        now_utc = datetime.now(timezone.utc)
        daily_opened: dict[str, int] = defaultdict(int)
        daily_closed: dict[str, int] = defaultdict(int)

        # Inizializziamo tutti i giorni dell'intervallo con 0
        date_keys: list[str] = []
        for i in range(days_bounded - 1, -1, -1):
            d_str = (now_utc - timedelta(days=i)).strftime("%Y-%m-%d")
            date_keys.append(d_str)
            daily_opened[d_str] = 0
            daily_closed[d_str] = 0

        # Timeline settimanale e mensile
        weekly_opened: dict[str, int] = defaultdict(int)
        weekly_closed: dict[str, int] = defaultdict(int)
        monthly_opened: dict[str, int] = defaultdict(int)
        monthly_closed: dict[str, int] = defaultdict(int)

        # Ripartizione per sezione
        section_active: dict[str, int] = defaultdict(int)
        section_closed: dict[str, int] = defaultdict(int)

        # Ripartizione per staff
        staff_claimed_active: dict[str, int] = defaultdict(int)
        staff_closed_total: dict[str, int] = defaultdict(int)
        all_staff_ids: set[str] = set()

        # SLA tracking
        sla_counts = {"breached": 0, "warning": 0, "pending": 0, "responded": 0}

        # Elaborazione ticket attivi
        for ticket in raw_active.values():
            if not isinstance(ticket, dict):
                continue
            sec_key = str(ticket.get("section") or "unspecified")
            section_active[sec_key] += 1

            claimed = ticket.get("claimed_by")
            if claimed:
                staff_str = str(claimed)
                staff_claimed_active[staff_str] += 1
                all_staff_ids.add(staff_str)

            sla_st = ticket_service.compute_sla_status(ticket)
            if sla_st in sla_counts:
                sla_counts[sla_st] += 1

            opened_at = ticket.get("opened_at")
            if opened_at and isinstance(opened_at, (int, float)):
                dt = datetime.fromtimestamp(opened_at, tz=timezone.utc)
                d_str = dt.strftime("%Y-%m-%d")
                w_str = dt.strftime("%Y-W%W")
                m_str = dt.strftime("%Y-%m")
                if d_str in daily_opened:
                    daily_opened[d_str] += 1
                weekly_opened[w_str] += 1
                monthly_opened[m_str] += 1

        # Elaborazione storico ticket
        durations: list[float] = []
        for entry in raw_history:
            if not isinstance(entry, dict):
                continue
            sec_key = str(entry.get("section") or "unspecified")
            section_closed[sec_key] += 1

            closed_by = entry.get("closed_by")
            claimed_by = entry.get("claimed_by")
            if closed_by:
                staff_str = str(closed_by)
                staff_closed_total[staff_str] += 1
                all_staff_ids.add(staff_str)
            elif claimed_by:
                staff_str = str(claimed_by)
                all_staff_ids.add(staff_str)

            opened_at = entry.get("opened_at")
            if opened_at and isinstance(opened_at, (int, float)):
                dt_open = datetime.fromtimestamp(opened_at, tz=timezone.utc)
                d_str = dt_open.strftime("%Y-%m-%d")
                w_str = dt_open.strftime("%Y-W%W")
                m_str = dt_open.strftime("%Y-%m")
                if d_str in daily_opened:
                    daily_opened[d_str] += 1
                weekly_opened[w_str] += 1
                monthly_opened[m_str] += 1

            closed_at = entry.get("closed_at")
            if closed_at and isinstance(closed_at, (int, float)):
                dt_close = datetime.fromtimestamp(closed_at, tz=timezone.utc)
                d_close_str = dt_close.strftime("%Y-%m-%d")
                w_close_str = dt_close.strftime("%Y-W%W")
                m_close_str = dt_close.strftime("%Y-%m")
                if d_close_str in daily_closed:
                    daily_closed[d_close_str] += 1
                weekly_closed[w_close_str] += 1
                monthly_closed[m_close_str] += 1

            dur = entry.get("duration_seconds")
            if dur is None and opened_at and closed_at and isinstance(closed_at, (int, float)) and isinstance(opened_at, (int, float)):
                dur = closed_at - opened_at
            if dur is not None and isinstance(dur, (int, float)) and dur >= 0:
                durations.append(float(dur))

        # Costruzione timeline giornaliera
        tickets_daily: list[DailyTicketCount] = [
            DailyTicketCount(
                date=d_str,
                opened_count=daily_opened[d_str],
                closed_count=daily_closed[d_str],
            )
            for d_str in date_keys
        ]

        # Costruzione timeline settimanale (ordinate per settimana)
        all_weeks = sorted(set(weekly_opened.keys()) | set(weekly_closed.keys()))[-12:]
        tickets_weekly: list[WeeklyTicketCount] = [
            WeeklyTicketCount(
                week=w_str,
                opened_count=weekly_opened[w_str],
                closed_count=weekly_closed[w_str],
            )
            for w_str in all_weeks
        ]

        # Costruzione timeline mensile (ordinate per mese)
        all_months = sorted(set(monthly_opened.keys()) | set(monthly_closed.keys()))[-12:]
        tickets_monthly: list[MonthlyTicketCount] = [
            MonthlyTicketCount(
                month=m_str,
                opened_count=monthly_opened[m_str],
                closed_count=monthly_closed[m_str],
            )
            for m_str in all_months
        ]

        # Statistiche per Sezione
        all_section_keys = set(sections.keys()) | set(section_active.keys()) | set(section_closed.keys())
        tickets_by_section: list[SectionStats] = []
        for sec_key in sorted(all_section_keys):
            sec_meta = sections.get(sec_key, {})
            act = section_active[sec_key]
            clo = section_closed[sec_key]
            tickets_by_section.append(
                SectionStats(
                    section=sec_key,
                    label=sec_meta.get("label"),
                    emoji=sec_meta.get("emoji"),
                    active_count=act,
                    closed_count=clo,
                    total_count=act + clo,
                )
            )

        # Statistiche per Staff
        tickets_by_staff: list[StaffActivityStats] = []
        for staff_id in sorted(all_staff_ids):
            st_name: Optional[str] = None
            if live_guild and staff_id.isdigit():
                m = live_guild.get_member(int(staff_id))
                if m:
                    st_name = m.display_name

            cl_act = staff_claimed_active[staff_id]
            cl_tot = staff_closed_total[staff_id]
            tickets_by_staff.append(
                StaffActivityStats(
                    staff_id=staff_id,
                    staff_name=st_name,
                    claimed_active=cl_act,
                    closed_total=cl_tot,
                    total_handled=cl_act + cl_tot,
                )
            )

        # Statistiche tempi di risoluzione
        if durations:
            res_stats = ResolutionTimeStats(
                average_seconds=round(sum(durations) / len(durations), 1),
                min_seconds=int(min(durations)),
                max_seconds=int(max(durations)),
                median_seconds=round(statistics.median(durations), 1),
                sample_size=len(durations),
            )
        else:
            res_stats = ResolutionTimeStats()

        # Statistiche SLA
        total_sla = sum(sla_counts.values())
        if total_sla > 0:
            breached = sla_counts["breached"]
            responded = sla_counts["responded"]
            comp_rate = round((responded / (responded + breached)) * 100.0, 1) if (responded + breached) > 0 else 100.0
        else:
            comp_rate = None

        sla_stats = SlaStatistics(
            sla_target_seconds=sla_seconds(gid_str),
            active_breached=sla_counts["breached"],
            active_warning=sla_counts["warning"],
            active_pending=sla_counts["pending"],
            active_responded=sla_counts["responded"],
            total_active_evaluated=total_sla,
            compliance_rate=comp_rate,
        )

        # Statistiche Candidature
        pending_apps = len(gconf.get("candidature_summary_messages") or {})
        
        # Conteggio sessioni DM in corso per questa guild
        dm_sessions = 0
        pref_sessions = cfg.prefs.get("candidature_sessions") or {}
        prefix = f"{gid_str}:"
        for key in pref_sessions:
            if str(key).startswith(prefix):
                dm_sessions += 1

        apps_stats = ApplicationsStatistics(
            pending_review=pending_apps,
            in_progress_dm=dm_sessions,
            historical_processed_total=None,  # Segnalato come None perché non persistito
            historical_data_available=False,
        )

        response = GuildStatisticsResponse(
            guild_id=gid_str,
            timeframe_days=days_bounded,
            tickets_daily=tickets_daily,
            tickets_weekly=tickets_weekly,
            tickets_monthly=tickets_monthly,
            tickets_by_section=tickets_by_section,
            tickets_by_staff=tickets_by_staff,
            resolution_time=res_stats,
            sla=sla_stats,
            applications=apps_stats,
            data_freshness_timestamp=int(now_ts),
        )

        self._cache[gid_str][f"stats_{days_bounded}"] = (now_ts, response)
        return response


stats_service = StatsService()
