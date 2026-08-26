import { apiRequest } from "./client";
import type { GuildDetail, GuildSummary, GuildUserProfile } from "./types";

export function getGuilds(token: string) {
  return apiRequest<GuildSummary[]>("/guilds", {}, token);
}

export function getGuildDetail(token: string, guildId: string) {
  return apiRequest<GuildDetail>(`/guilds/${encodeURIComponent(guildId)}`, {}, token);
}

export function getGuildMe(token: string, guildId: string) {
  return apiRequest<GuildUserProfile>(`/guilds/${encodeURIComponent(guildId)}/me`, {}, token);
}
