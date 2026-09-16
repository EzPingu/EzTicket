import { Check, ChevronLeft, MessageCircle, Search, X } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError } from "../../api/client";
import { acceptApplication, getApplicationDetail, getApplications, rejectApplication, sendApplicationDm } from "../../api/applications.api";
import type { ApplicationDetail, ApplicationSummary } from "../../api/types";

type Props = { token: string; guildId: string; isRefreshing: boolean; onUnauthorized: () => void };

export function ApplicationsWorkspace({ token, guildId, isRefreshing, onUnauthorized }: Props) {
  const [items, setItems] = useState<ApplicationSummary[]>([]);
  const [selected, setSelected] = useState<ApplicationDetail | null>(null);
  const [search, setSearch] = useState("");
  const [modal, setModal] = useState<"dm" | "reject" | null>(null);
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fail = (cause: unknown) => {
    if (cause instanceof ApiError && cause.status === 401) { onUnauthorized(); return; }
    setError(cause instanceof Error ? cause.message : "Impossibile caricare le candidature.");
  };
  const load = async () => {
    setLoading(true); setError(null);
    try { setItems(await getApplications(token, guildId)); } catch (cause) { fail(cause); } finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, [token, guildId]);
  useEffect(() => { if (isRefreshing) void load(); }, [isRefreshing]);

  const open = async (item: ApplicationSummary) => {
    try { setSelected(await getApplicationDetail(token, guildId, item.user_id)); } catch (cause) { fail(cause); }
  };
  const action = async (kind: "accept" | "reject" | "dm") => {
    if (!selected) return;
    try {
      if (kind === "accept") await acceptApplication(token, guildId, selected.user_id);
      if (kind === "reject") await rejectApplication(token, guildId, selected.user_id, text);
      if (kind === "dm") await sendApplicationDm(token, guildId, selected.user_id, text);
      setModal(null); setText("");
      if (kind !== "dm") { setSelected(null); await load(); }
      else setSelected(await getApplicationDetail(token, guildId, selected.user_id));
    } catch (cause) { fail(cause); }
  };
  const visible = items.filter((item) => `${item.candidate_name ?? ""} ${item.candidature_type ?? ""} ${item.user_id}`.toLowerCase().includes(search.toLowerCase()));
  if (selected) return <ApplicationDetailView application={selected} onBack={() => setSelected(null)} onAction={action} modal={modal} text={text} setModal={setModal} setText={setText} />;
  return <section className="application-workspace">
    <div className="page-heading"><div><span className="eyebrow">Workspace / Community</span><h1>Candidature</h1><p>Valuta le candidature del server selezionato.</p></div><strong>{items.filter((item) => item.status === "pending_review" || item.status === "pending").length} in attesa</strong></div>
    <label className="ticket-search"><Search size={15} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Cerca candidato o tipo..." /></label>
    {error && <div className="ticket-error">{error}<button onClick={() => void load()}>Riprova</button></div>}
    {loading ? <div className="ticket-empty">Caricamento candidature...</div> : <div className="application-card-grid">{visible.map((item) => <button className="application-card" key={item.user_id} onClick={() => void open(item)}><ApplicationAvatar src={item.candidate_avatar} name={item.candidate_name} /><span><strong>{item.candidate_name ?? `Utente ${item.user_id}`}</strong><small>{item.candidature_type ?? "Candidatura"}</small><small>Inviata da {item.staffer_name ?? "Sistema"}</small></span><em className={item.status === "accepted" ? "application-status-accepted" : item.status === "rejected" ? "application-status-rejected" : "application-status-pending"}>{item.status === "accepted" ? "Accettata" : item.status === "rejected" ? "Rifiutata" : "In attesa"}</em></button>)}</div>}
  </section>;
}

function ApplicationDetailView({ application, onBack, onAction, modal, text, setModal, setText }: { application: ApplicationDetail; onBack: () => void; onAction: (kind: "accept" | "reject" | "dm") => Promise<void>; modal: "dm" | "reject" | null; text: string; setModal: (value: "dm" | "reject" | null) => void; setText: (value: string) => void }) {
  return <section className="application-detail ticket-detail-view"><button className="ticket-back" onClick={onBack}><ChevronLeft size={17} />Torna alle candidature</button><div className="ticket-detail-header"><ApplicationAvatar src={application.candidate_avatar} name={application.candidate_name} large /><div><span className="eyebrow">{application.candidature_type ?? "Candidatura"}</span><h1>{application.candidate_name ?? `Utente ${application.user_id}`}</h1><p>ID Discord: {application.user_id}</p><p>Inviata da {application.staffer_name ?? "Sistema"} · {formatDate(application.created_at)}</p></div><span className="application-status-pending">{application.status}</span></div><div className="application-answers">{application.questions.map((question, index) => <article key={`${question}-${index}`}><strong>{question}</strong><p>{application.answers[index] || "Nessuna risposta"}</p></article>)}</div>{application.dm_messages.length > 0 && <div className="application-dm-log">{application.dm_messages.map((message, index) => <article key={`${message.created_at}-${index}`}><strong>{message.author_name ?? (message.direction === "staff" ? "Staff" : application.candidate_name)}</strong><p>{message.content}</p></article>)}</div>}<div className="application-actions"><button className="secondary-button" onClick={() => void onAction("accept")}><Check size={15} />Accetta</button><button className="secondary-button" onClick={() => setModal("dm")}><MessageCircle size={15} />Scrivi in DM</button><button className="danger-button" onClick={() => setModal("reject")}><X size={15} />Rifiuta</button></div>{modal && <div className="modal-backdrop"><div className="ticket-modal"><button className="modal-close" onClick={() => setModal(null)}><X size={17} /></button><h2>{modal === "dm" ? "Scrivi in DM" : "Rifiuta candidatura"}</h2><p>{modal === "dm" ? "Il messaggio verrà inviato al candidato." : "Inserisci il motivo del rifiuto."}</p><textarea value={text} onChange={(event) => setText(event.target.value)} autoFocus maxLength={4000} /><div className="modal-actions"><button className="secondary-button" onClick={() => setModal(null)}>Annulla</button><button className={modal === "dm" ? "secondary-button" : "danger-button"} disabled={!text.trim()} onClick={() => void onAction(modal)}>{modal === "dm" ? "Invia" : "Rifiuta"}</button></div></div></div>}</section>;
}

function ApplicationAvatar({ src, name, large }: { src: string | null; name: string | null; large?: boolean }) {
  return <div className={`ticket-avatar ${large ? "ticket-avatar--large" : ""}`}>{src ? <img src={src} alt="" /> : (name ?? "?").slice(0, 2).toUpperCase()}</div>;
}
function formatDate(timestamp: number) { return timestamp ? new Date(timestamp * 1000).toLocaleString("it-IT") : "N/D"; }
