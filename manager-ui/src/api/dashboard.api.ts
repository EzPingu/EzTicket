import { apiRequest } from "./client";
import type { DashboardSummary } from "./types";

export function getDashboard(token: string, guildId: string, forceRefresh = false) {
  const query = forceRefresh ? "?force_refresh=true" : "";
  return apiRequest<DashboardSummary>(
    `/guilds/${encodeURIComponent(guildId)}/dashboard${query}`,
    {},
    token,
  );
}
