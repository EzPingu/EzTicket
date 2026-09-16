import { CalendarDays, ChevronLeft, FileText, Hash, MessageSquare, Search, Send, ShieldCheck, Ticket as TicketIcon, UserRound, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { ApiError } from "../../api/client";
import { getTicketHistory, getTicketHistoryDetail } from "../../api/history.api";
import { closeTicket, getActiveTicketDetail, getActiveTickets, replyToTicket, streamTicketMessages } from "../../api/tickets.api";
import type { TicketDetail, TicketHistoryDetail, TicketHistoryItem, TicketSummary } from "../../api/types";

type Props = { token: string; guildId: string; isRefreshing: boolean; onUnauthorized: () => void };
type Filter = "all" | "open" | "closed";

export function TicketWorkspace({ token, guildId, isRefreshing, onUnauthorized }: Props) {
  const [openTickets, setOpenTickets] = useState<TicketSummary[]>([]);
  const [closedTickets, setClosedTickets] = useState<TicketHistoryItem[]>([]);
  const [selected, setSelected] = useState<{ kind: "open"; ticket: TicketDetail } | { kind: "closed"; ticket: TicketHistoryDetail } | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [section, setSection] = useState("all");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [closeOpen, setCloseOpen] = useState(false);
  const [closeReason, setCloseReason] = useState("");
  const [closeLoading, setCloseLoading] = useState(false);

  const handleError = (cause: unknown) => {
    if (cause instanceof ApiError && cause.status === 401) {
      onUnauthorized();
      return;
    }
    setError(cause instanceof Error ? cause.message : "Impossibile caricare i ticket.");
  };

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [active, history] = await Promise.all([
        getActiveTickets(token, guildId),
        getTicketHistory(token, guildId),
      ]);
      setOpenTickets(active);
      setClosedTickets(history.items);
    } catch (cause) {
      handleError(cause);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, [token, guildId]);
  useEffect(() => { if (isRefreshing) void load(); }, [isRefreshing]);

  const sections = useMemo(() => [...new Set([...openTickets, ...closedTickets].map((ticket) => ticket.section_label || ticket.section).filter(Boolean))], [openTickets, closedTickets]);
  const matches = (ticket: TicketSummary | TicketHistoryItem) => {
    const text = `${ticket.number} ${ticket.opener_name ?? ""} ${ticket.motivo ?? ""} ${ticket.section_label ?? ticket.section}`.toLowerCase();
    return (!search.trim() || text.includes(search.toLowerCase().trim())) && (section === "all" || (ticket.section_label || ticket.section) === section);
  };
  const filteredOpen = openTickets.filter(matches);
  const filteredClosed = closedTickets.filter(matches);

  const openDetail = async (ticket: TicketSummary) => {
    setDetailLoading(true);
    setError(null);
    try {
      setSelected({ kind: "open", ticket: await getActiveTicketDetail(token, guildId, ticket.channel_id) });
    } catch (cause) {
      handleError(cause);
    } finally {
      setDetailLoading(false);
    }
  };
  const closedDetail = async (ticket: TicketHistoryItem) => {
    setDetailLoading(true);
    setError(null);
    try {
      setSelected({ kind: "closed", ticket: await getTicketHistoryDetail(token, guildId, ticket.number) });
    } catch (cause) {
      handleError(cause);
    } finally {
      setDetailLoading(false);
    }
  };

  const submitClose = async () => {
    if (!selected || selected.kind !== "open") return;
    setCloseLoading(true);
    try {
      await closeTicket(token, guildId, selected.ticket.channel_id, closeReason);
      setCloseOpen(false);
      setCloseReason("");
      setSelected(null);
      await load();
    } catch (cause) {
      handleError(cause);
    } finally {
      setCloseLoading(false);
    }
  };

  const submitReply = async (message: string) => {
    if (!selected || selected.kind !== "open") return;
    try {
      await replyToTicket(token, guildId, selected.ticket.channel_id, message);
      setSelected({ kind: "open", ticket: await getActiveTicketDetail(token, guildId, selected.ticket.channel_id) });
    } catch (cause) {
      handleError(cause);
      throw cause;
    }
  };

  useEffect(() => {
    if (!selected || selected.kind !== "open") return;
    const channelId = selected.ticket.channel_id;
    const controller = new AbortController();
    let realtimeAvailable = true;
    const refreshMessages = async () => {
      try {
        const detail = await getActiveTicketDetail(token, guildId, channelId);
        setSelected((current) => (
          current?.kind === "open" && current.ticket.channel_id === channelId
            ? { kind: "open", ticket: detail }
            : current
        ));
      } catch (cause) {
        handleError(cause);
      }
    };
    void streamTicketMessages(token, guildId, channelId, controller.signal, (message) => {
      setSelected((current) => {
        if (current?.kind !== "open" || current.ticket.channel_id !== channelId) return current;
        const messages = current.ticket.messages ?? [];
        if (messages.some((item) => item.id === message.id)) return current;
        return {
          kind: "open",
          ticket: { ...current.ticket, messages: [...messages, message].sort((a, b) => a.created_at - b.created_at) },
        };
      });
    }).catch(() => {
      realtimeAvailable = false;
      void refreshMessages();
    });
    const fallbackIntervalId = window.setInterval(() => {
      if (!realtimeAvailable) void refreshMessages();
    }, 5000);
    return () => {
      controller.abort();
      window.clearInterval(fallbackIntervalId);
    };
  }, [selected?.kind, selected?.kind === "open" ? selected.ticket.channel_id : null, token, guildId]);

  if (selected) {
    return <><TicketDetailView selected={selected} onBack={() => setSelected(null)} onClose={() => setCloseOpen(true)} onTranscript={() => {
      if (selected.kind === "closed" && selected.ticket.transcript_url) window.open(selected.ticket.transcript_url, "_blank", "noopener,noreferrer");
    }} onReply={submitReply} error={error} /><CloseTicketModal open={closeOpen} reason={closeReason} loading={closeLoading} onReasonChange={setCloseReason} onCancel={() => setCloseOpen(false)} onConfirm={() => void submitClose()} /></>;
  }

  return <section className="ticket-workspace">
    <div className="page-heading ticket-heading">
      <div><span className="eyebrow">Workspace / Support</span><h1>Ticket</h1><p>Gestisci le richieste della community e consulta lo storico.</p></div>
      <div className="ticket-counts"><strong>{openTickets.length}</strong><span>aperti</span><strong>{closedTickets.length}</strong><span>chiusi</span></div>
    </div>
    <div className="ticket-toolbar">
      <label className="ticket-search"><Search size={15} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Cerca ticket, utente o motivo..." /></label>
      <select value={filter} onChange={(event) => setFilter(event.target.value as Filter)}><option value="all">Tutti</option><option value="open">Aperti</option><option value="closed">Chiusi</option></select>
      <select value={section} onChange={(event) => setSection(event.target.value)}><option value="all">Tutte le sezioni</option>{sections.map((item) => <option key={item} value={item}>{item}</option>)}</select>
    </div>
    {error && <div className="ticket-error">{error}<button onClick={() => void load()}>Riprova</button></div>}
    {loading ? <div className="ticket-empty"><span className="button-spinner" />Caricamento ticket...</div> : <div className="ticket-lists">
      {filter !== "closed" && <TicketList title="Ticket Aperti" icon={<TicketIcon size={17} />} tickets={filteredOpen} open onSelect={(ticket) => { if ("channel_id" in ticket) void openDetail(ticket); }} />}
      {filter === "all" && <div className="ticket-separator"><span>Ticket Chiusi</span></div>}
      {filter !== "open" && <TicketList title="Ticket Chiusi" icon={<FileText size={17} />} tickets={filteredClosed} onSelect={(ticket) => { if (!("channel_id" in ticket)) void closedDetail(ticket); }} />}
      {!filteredOpen.length && !filteredClosed.length && <div className="ticket-empty">Nessun ticket corrisponde ai filtri selezionati.</div>}
    </div>}
    {detailLoading && <div className="ticket-detail-loading"><span className="button-spinner" />Apertura ticket...</div>}
    <CloseTicketModal open={closeOpen} reason={closeReason} loading={closeLoading} onReasonChange={setCloseReason} onCancel={() => setCloseOpen(false)} onConfirm={() => void submitClose()} />
  </section>;
}

function CloseTicketModal({ open, reason, loading, onReasonChange, onCancel, onConfirm }: { open: boolean; reason: string; loading: boolean; onReasonChange: (value: string) => void; onCancel: () => void; onConfirm: () => void }) {
  if (!open) return null;
  return <div className="modal-backdrop"><div className="ticket-modal"><button className="modal-close" onClick={onCancel}><X size={17} /></button><h2>Chiudi Ticket</h2><p>Inserisci il motivo della chiusura.</p><textarea value={reason} onChange={(event) => onReasonChange(event.target.value)} maxLength={500} placeholder="Motivo opzionale..." /><div className="modal-actions"><button className="secondary-button" onClick={onCancel}>Annulla</button><button className="danger-button" disabled={loading} onClick={onConfirm}>{loading ? "Chiusura..." : "Chiudi Ticket"}</button></div></div></div>;
}

function TicketList({ title, icon, tickets, open, onSelect }: { title: string; icon: ReactNode; tickets: (TicketSummary | TicketHistoryItem)[]; open?: boolean; onSelect: (ticket: TicketSummary | TicketHistoryItem) => void }) {
  const select = (ticket: TicketSummary | TicketHistoryItem) => {
    if (open && "channel_id" in ticket) onSelect(ticket);
    if (!open && !("channel_id" in ticket)) onSelect(ticket);
  };
  return <section className="ticket-list-section"><div className="ticket-list-heading"><h2>{icon}{title}</h2><span>{tickets.length}</span></div>{tickets.length ? <div className="ticket-card-grid">{tickets.map((ticket) => <button className="ticket-card" key={`${ticket.guild_id}-${ticket.number}-${"channel_id" in ticket ? ticket.channel_id : ticket.channel_name}`} onClick={() => select(ticket)}><TicketAvatar src={ticket.opener_avatar} name={ticket.opener_name} /><div className="ticket-card-copy"><strong>{ticket.opener_name ?? `Utente ${ticket.opener_id}`}</strong><span>{ticket.section_emoji ?? "🎫"} {ticket.motivo || "Richiesta senza motivo"}</span><small>Sezione: {ticket.section_label || ticket.section}</small><small>Ticket: #{ticket.number}</small></div><em className={open ? "ticket-status-open" : "ticket-status-closed"}>{open ? "Aperto" : "Chiuso"}</em></button>)}</div> : <div className="ticket-list-empty">Nessun ticket {open ? "aperto" : "chiuso"}.</div>}</section>;
}

function TicketDetailView({ selected, onBack, onClose, onTranscript, onReply, error }: { selected: { kind: "open"; ticket: TicketDetail } | { kind: "closed"; ticket: TicketHistoryDetail }; onBack: () => void; onClose: () => void; onTranscript: () => void; onReply: (message: string) => Promise<void>; error: string | null }) {
  const ticket = selected.ticket;
  const isOpen = selected.kind === "open";
  const [reply, setReply] = useState("");
  const [replyLoading, setReplyLoading] = useState(false);
  const messages = ticket.messages ?? [];
  const openerName = ticket.opener_name ?? `Utente ${ticket.opener_id}`;
  const submitReply = async () => {
    if (!reply.trim() || replyLoading) return;
    setReplyLoading(true);
    try {
      await onReply(reply);
      setReply("");
    } catch {
      // The workspace owns and displays the API error.
    } finally {
      setReplyLoading(false);
    }
  };
  return <section className="ticket-detail-view">{error && <div className="ticket-error">{error}</div>}<button className="ticket-back" onClick={onBack}><ChevronLeft size={17} />Torna ai ticket</button><div className="ticket-detail-header"><TicketAvatar src={ticket.opener_avatar} name={ticket.opener_name} large /><div><span className="eyebrow">Ticket #{ticket.number}</span><h1>{ticket.motivo || "Richiesta ticket"}</h1><p><UserRound size={13} />{openerName} · <Hash size={12} />{ticket.opener_id}</p><p><MessageSquare size={13} />{ticket.section_label || ticket.section} · <CalendarDays size={13} />Aperto il {formatDate(isOpen ? ticket.created_at : ticket.opened_at)}</p></div><span className={isOpen ? "ticket-status-open" : "ticket-status-closed"}>{isOpen ? "Aperto" : "Chiuso"}</span></div>{!isOpen && <div className="closed-summary"><span>Chiuso il <strong>{formatDate(ticket.closed_at)}</strong></span><span>Da <strong>{ticket.closed_by_name ?? ticket.closed_by ?? "Sistema"}</strong></span><span>Motivo: <strong>{ticket.close_reason || "Non specificato"}</strong></span></div>}<div className="ticket-conversation">{messages.length ? messages.map((message) => <div className={`ticket-message ${message.is_staff ? "ticket-message--staff" : ""}`} key={message.id}><TicketAvatar src={message.author_avatar} name={message.author_name} /><div><div className="ticket-message-meta"><strong>{message.author_name}</strong>{message.is_staff && <ShieldCheck size={12} />}<time>{formatDate(message.created_at)}</time></div>{message.content && <p>{message.content}</p>}{message.attachments.map((attachment) => <a key={attachment} href={attachment} target="_blank" rel="noreferrer"><Send size={12} />Allegato</a>)}</div></div>) : <div className="ticket-list-empty">{isOpen ? "Nessun messaggio disponibile." : "Conversazione disponibile nel transcript Discord."}</div>}</div>{isOpen && <div className="ticket-reply-box"><textarea value={reply} onChange={(event) => setReply(event.target.value)} placeholder="Scrivi una risposta..." maxLength={4000} /><button className="secondary-button" disabled={!reply.trim() || replyLoading} onClick={() => void submitReply()}>{replyLoading ? "Invio..." : "Invia"}</button></div>}<div className="ticket-detail-actions">{isOpen ? <button className="danger-button" onClick={onClose}>🔴 Chiudi Ticket</button> : <button className="secondary-button" disabled={!ticket.transcript_url} onClick={onTranscript}><FileText size={15} />Visualizza Transcript</button>}</div></section>;
}

function TicketAvatar({ src, name, large }: { src: string | null; name: string | null; large?: boolean }) {
  return <div className={`ticket-avatar ${large ? "ticket-avatar--large" : ""}`}>{src ? <img src={src} alt="" /> : (name ?? "?").slice(0, 2).toUpperCase()}</div>;
}

function formatDate(timestamp: number | null | undefined) {
  return timestamp ? new Date(timestamp * 1000).toLocaleString("it-IT") : "N/D";
}
