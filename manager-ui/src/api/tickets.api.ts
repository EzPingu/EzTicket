import { apiRequest } from "./client";
import type { TicketCloseResponse, TicketDetail, TicketReplyResponse, TicketSummary } from "./types";

export function getActiveTickets(token: string, guildId: string) {
  return apiRequest<TicketSummary[]>(`/guilds/${encodeURIComponent(guildId)}/tickets/active`, {}, token);
}

export function getActiveTicketDetail(token: string, guildId: string, channelId: string) {
  return apiRequest<TicketDetail>(`/guilds/${encodeURIComponent(guildId)}/tickets/active/${encodeURIComponent(channelId)}`, {}, token);
}

export function closeTicket(token: string, guildId: string, channelId: string, reason: string) {
  return apiRequest<TicketCloseResponse>(`/guilds/${encodeURIComponent(guildId)}/tickets/active/${encodeURIComponent(channelId)}/close`, {
    method: "POST",
    body: JSON.stringify({ reason: reason.trim() || null }),
  }, token);
}

export function replyToTicket(token: string, guildId: string, channelId: string, message: string) {
  return apiRequest<TicketReplyResponse>(`/guilds/${encodeURIComponent(guildId)}/tickets/active/${encodeURIComponent(channelId)}/reply`, {
    method: "POST",
    body: JSON.stringify({ message }),
  }, token);
}
