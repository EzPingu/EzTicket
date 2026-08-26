import { apiRequest } from "./client";
import type { TicketHistoryDetail, TicketHistoryItem } from "./types";

type HistoryPage = {
  items: TicketHistoryItem[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
};

export function getTicketHistory(token: string, guildId: string, pageSize = 100) {
  return apiRequest<HistoryPage>(`/guilds/${encodeURIComponent(guildId)}/tickets/history?page_size=${pageSize}&sort_by=closed_at&order=desc`, {}, token);
}

export function getTicketHistoryDetail(token: string, guildId: string, ticketNumber: number) {
  return apiRequest<TicketHistoryDetail>(`/guilds/${encodeURIComponent(guildId)}/tickets/history/${ticketNumber}`, {}, token);
}
