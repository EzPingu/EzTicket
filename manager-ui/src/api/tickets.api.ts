import { API_BASE_URL, APP_VERSION, apiRequest } from "./client";
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

export async function streamTicketMessages(
  token: string,
  guildId: string,
  channelId: string,
  signal: AbortSignal,
  onMessage: (message: TicketDetail["messages"][number]) => void,
) {
  const response = await fetch(
    `${API_BASE_URL}/guilds/${encodeURIComponent(guildId)}/tickets/active/${encodeURIComponent(channelId)}/events`,
    { headers: { Authorization: "Bearer " + token, "X-Manager-Version": APP_VERSION }, signal },
  );
  if (!response.ok || !response.body) {
    throw new Error("Connessione realtime non disponibile.");
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += value;
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const event of events) {
      const data = event.split("\n").find((line) => line.startsWith("data: "));
      if (data) onMessage(JSON.parse(data.slice(6)));
    }
  }
}
