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
class TicketSummaryResponse(BaseModel):
    guild_id: str
    channel_id: str
    number: int
    opener_id: str
    opener_name: Optional[str] = None
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
    message: str = "Ticket chiuso con successo"


# --- Error Model ---
class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
    code: Optional[str] = None

