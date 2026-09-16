import { apiRequest } from "./client";
import type { ApplicationDetail, ApplicationSummary } from "./types";

export function getApplications(token: string, guildId: string) {
  return apiRequest<ApplicationSummary[]>(`/guilds/${encodeURIComponent(guildId)}/applications`, {}, token);
}

export function getApplicationDetail(token: string, guildId: string, userId: string) {
  return apiRequest<ApplicationDetail>(`/guilds/${encodeURIComponent(guildId)}/applications/${encodeURIComponent(userId)}`, {}, token);
}

export function acceptApplication(token: string, guildId: string, userId: string) {
  return apiRequest(`/guilds/${encodeURIComponent(guildId)}/applications/${encodeURIComponent(userId)}/accept`, { method: "POST", body: JSON.stringify({ full_onboard: true }) }, token);
}

export function rejectApplication(token: string, guildId: string, userId: string, reason: string) {
  return apiRequest(`/guilds/${encodeURIComponent(guildId)}/applications/${encodeURIComponent(userId)}/reject`, { method: "POST", body: JSON.stringify({ reason: reason.trim() || null }) }, token);
}

export function sendApplicationDm(token: string, guildId: string, userId: string, message: string) {
  return apiRequest(`/guilds/${encodeURIComponent(guildId)}/applications/${encodeURIComponent(userId)}/dm`, { method: "POST", body: JSON.stringify({ message }) }, token);
}
