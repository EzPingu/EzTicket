"""
manager_backend/models/schemas.py
Modelli Pydantic per le richieste e risposte API di EzTicket Manager.
"""
from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field


# --- Health & Info ---
class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
    timestamp: int


# --- Auth & OAuth ---
class OAuthExchangeRequest(BaseModel):
    code: str = Field(..., min_length=1, description="Authorization Code restituito da Discord")
    code_verifier: str = Field(..., min_length=10, description="PKCE code_verifier originato dal client")
    redirect_uri: Optional[str] = Field(None, description="Redirect URI impiegato nel flusso")


class UserProfileResponse(BaseModel):
    id: str
    username: str
    global_name: Optional[str] = None
    avatar: Optional[str] = None
    is_bot_operator: bool = False


class AuthSessionResponse(BaseModel):
    session_token: str
    expires_at: int
    user: UserProfileResponse


class LogoutResponse(BaseModel):
    success: bool = True
    message: str = "Sessione revocata con successo"


# --- Guild Models ---
class GuildSummaryResponse(BaseModel):
    id: str
    name: Optional[str] = None
    icon: Optional[str] = None
    member_count: Optional[int] = None
    role: str  # "GUILD_ADMIN" | "GUILD_STAFF"
    is_owner: bool = False
    is_admin: bool = False
    active_tickets_count: int = 0


class GuildDetailResponse(BaseModel):
    id: str
    name: Optional[str] = None
    icon: Optional[str] = None
    user_role: str
    is_owner: bool = False
    is_admin: bool = False
    sections_count: int = 0
    branding: Optional[str] = None
    sla_seconds: int = 300
    claim_timeout_seconds: int = 120
    inactivity_hours: int = 24


# --- Ticket Models ---
class TicketMessageResponse(BaseModel):
    id: str
    author_id: str
    author_name: str
    author_avatar: Optional[str] = None
    content: str = ""
    created_at: int
    attachments: list[str] = Field(default_factory=list)
    is_staff: bool = False


class TicketSummaryResponse(BaseModel):
    guild_id: str
    channel_id: str
    number: int
    opener_id: str
    opener_name: Optional[str] = None
    opener_avatar: Optional[str] = None
    section: str
    section_label: Optional[str] = None
    section_emoji: Optional[str] = None
    claimed_by: Optional[str] = None
    status: str = "open"
    created_at: int
    motivo: Optional[str] = None
    sla_deadline: Optional[int] = None
    sla_status: str = "pending"  # "pending" | "warning" | "breached" | "responded"
    sla_notified: bool = False
    first_response_at: Optional[int] = None
    claim_deadline: Optional[int] = None
    claimer_responded: bool = False


class TicketDetailResponse(BaseModel):
    guild_id: str
    channel_id: str
    number: int
    opener_id: str
    opener_name: Optional[str] = None
    opener_avatar: Optional[str] = None
    section: str
    section_label: Optional[str] = None
    section_emoji: Optional[str] = None
    claimed_by: Optional[str] = None
    status: str = "open"
    created_at: int
    motivo: Optional[str] = None
    sla_deadline: Optional[int] = None
    sla_status: str = "pending"
    sla_notified: bool = False
    first_response_at: Optional[int] = None
    claim_deadline: Optional[int] = None
    claimer_responded: bool = False
    added_members: list[str] = Field(default_factory=list)
    notes_count: int = 0
    notes: list[dict[str, Any]] = Field(default_factory=list)
    messages: list[TicketMessageResponse] = Field(default_factory=list)
    transcript_url: Optional[str] = None


class TicketClaimResponse(BaseModel):
    success: bool = True
    guild_id: str
    channel_id: str
    claimed_by: Optional[str] = None
    claimed: bool
    message: str


class TicketCloseRequest(BaseModel):
    reason: Optional[str] = Field(None, max_length=500, description="Motivo opzionale della chiusura")


class TicketCloseResponse(BaseModel):
    success: bool = True
    guild_id: str
    channel_id: str
    closed_by: str
    closed_at: int
    close_reason: Optional[str] = None
    transcript_url: Optional[str] = None
    message: str = "Ticket chiuso con successo"


class TicketReplyRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


class TicketReplyResponse(BaseModel):
    success: bool = True
    guild_id: str
    channel_id: str
    message_id: str
    message: str = "Messaggio inviato nel ticket"


# --- Error Model ---
class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
    code: Optional[str] = None


# --- Guild User Profile ---
class GuildUserProfileResponse(UserProfileResponse):
    guild_id: str
    guild_role: str
    is_owner: bool
    is_admin: bool

# --- Applications (Candidature) ---
class ApplicationSummaryResponse(BaseModel):
    guild_id: str
    user_id: str
    channel_id: str
    message_id: str
    notice_message_id: Optional[str] = None
    created_at: int
    status: str = "pending_review"

class ApplicationDetailResponse(ApplicationSummaryResponse):
    questions: list[str] = Field(default_factory=list)
    answers: list[str] = Field(default_factory=list)
    qa_available: bool = False

class ApplicationAcceptRequest(BaseModel):
    full_onboard: bool = True

class ApplicationAcceptResponse(BaseModel):
    success: bool
    guild_id: str
    user_id: str
    roles_added: list[str] = Field(default_factory=list)
    nickname_changed: bool = False
    dm_sent: bool = False
    summary_updated: bool = False

class ApplicationRejectRequest(BaseModel):
    reason: Optional[str] = Field(None, max_length=500)

class ApplicationRejectResponse(BaseModel):
    success: bool
    guild_id: str
    user_id: str
    reason: Optional[str] = None
    dm_sent: bool = False
    summary_updated: bool = False


class LatestMemberResponse(BaseModel):
    id: str
    username: str
    global_name: Optional[str] = None
    avatar: Optional[str] = None
    joined_at: int


class OnlineStaffMemberResponse(BaseModel):
    id: str
    username: str
    avatar: Optional[str] = None
    role: Optional[str] = None
    roles: list[str] = Field(default_factory=list)
    status: str


# --- Dashboard Schemas ---
class DashboardSummaryResponse(BaseModel):
    guild_id: str
    server_member_count: Optional[int] = None
    staff_member_count: Optional[int] = None
    latest_member: Optional[LatestMemberResponse] = None
    online_staff: list[OnlineStaffMemberResponse] = Field(default_factory=list)
    active_tickets: int
    closed_tickets_total: int
    pending_applications: int
    tickets_today: int
    tickets_this_week: int
    tickets_this_month: int
    average_resolution_time_seconds: Optional[float] = None
    average_first_response_time_seconds: Optional[float] = None
    sla_compliance_rate: Optional[float] = None
    tickets_by_section: dict[str, int] = Field(default_factory=dict)
    tickets_by_status: dict[str, int] = Field(default_factory=dict)
    sla_summary: dict[str, int] = Field(default_factory=dict)
    data_freshness_timestamp: int


# --- History Schemas ---
class TicketHistoryItemResponse(BaseModel):
    guild_id: str
    number: int
    channel_name: str
    section: str
    section_label: Optional[str] = None
    section_emoji: Optional[str] = None
    motivo: Optional[str] = None
    opener_id: str
    opener_name: Optional[str] = None
    opener_avatar: Optional[str] = None
    claimed_by: Optional[str] = None
    closed_by: Optional[str] = None
    closed_by_name: Optional[str] = None
    closed_by_avatar: Optional[str] = None
    close_reason: Optional[str] = None
    opened_at: Optional[int] = None
    closed_at: Optional[int] = None
    duration_seconds: Optional[int] = None
    transcript_sent: bool = False
    transcript_url: Optional[str] = None
    notes_count: int = 0
    channel_deleted: bool = False


class TicketHistoryDetailResponse(TicketHistoryItemResponse):
    added_members: list[str] = Field(default_factory=list)
    notes: list[dict[str, Any]] = Field(default_factory=list)
    messages: list[TicketMessageResponse] = Field(default_factory=list)
    transcript_url: Optional[str] = None


class PaginatedTicketHistoryResponse(BaseModel):
    guild_id: str
    items: list[TicketHistoryItemResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


# --- Statistics Schemas ---
class DailyTicketCount(BaseModel):
    date: str  # YYYY-MM-DD
    opened_count: int
    closed_count: int


class WeeklyTicketCount(BaseModel):
    week: str  # YYYY-Www
    opened_count: int
    closed_count: int


class MonthlyTicketCount(BaseModel):
    month: str  # YYYY-MM
    opened_count: int
    closed_count: int


class SectionStats(BaseModel):
    section: str
    label: Optional[str] = None
    emoji: Optional[str] = None
    active_count: int
    closed_count: int
    total_count: int


class StaffActivityStats(BaseModel):
    staff_id: str
    staff_name: Optional[str] = None
    claimed_active: int
    closed_total: int
    total_handled: int


class ResolutionTimeStats(BaseModel):
    average_seconds: Optional[float] = None
    min_seconds: Optional[int] = None
    max_seconds: Optional[int] = None
    median_seconds: Optional[float] = None
    sample_size: int = 0


class SlaStatistics(BaseModel):
    sla_target_seconds: int
    active_breached: int
    active_warning: int
    active_pending: int
    active_responded: int
    total_active_evaluated: int
    compliance_rate: Optional[float] = None


class ApplicationsStatistics(BaseModel):
    pending_review: int
    in_progress_dm: int
    historical_processed_total: Optional[int] = None
    historical_data_available: bool = False


class GuildStatisticsResponse(BaseModel):
    guild_id: str
    timeframe_days: int
    tickets_daily: list[DailyTicketCount] = Field(default_factory=list)
    tickets_weekly: list[WeeklyTicketCount] = Field(default_factory=list)
    tickets_monthly: list[MonthlyTicketCount] = Field(default_factory=list)
    tickets_by_section: list[SectionStats] = Field(default_factory=list)
    tickets_by_staff: list[StaffActivityStats] = Field(default_factory=list)
    resolution_time: ResolutionTimeStats
    sla: SlaStatistics
    applications: ApplicationsStatistics
    data_freshness_timestamp: int