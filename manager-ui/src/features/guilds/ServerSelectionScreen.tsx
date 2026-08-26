import { ArrowRight, Users } from "lucide-react";
import type { GuildSummary } from "../../api/types";

type Props = { guilds: GuildSummary[]; loading: boolean; error: string | null; onSelect: (guild: GuildSummary) => void; onRetry: () => void; };
export function ServerSelectionScreen({ guilds, loading, error, onSelect, onRetry }: Props) {
  return <main className="server-screen">
    <div className="server-heading"><span className="eyebrow">EzTicket Manager</span><h1>Quale server vuoi gestire?</h1><p>Seleziona il server Discord da amministrare.</p></div>
    {loading && <div className="state-card"><span className="button-spinner" /><span>Caricamento dei tuoi server...</span></div>}
    {error && <div className="state-card state-card--error"><strong>Non riesco a caricare i server</strong><span>{error}</span><button className="secondary-button" onClick={onRetry}>Riprova</button></div>}
    {!loading && !error && guilds.length === 0 && <div className="state-card"><strong>Nessun server disponibile</strong><span>Non hai ancora accesso a un server gestibile.</span></div>}
    {!loading && !error && guilds.length > 0 && <div className="guild-grid">{guilds.map((guild) => <button className="guild-card" key={guild.id} onClick={() => onSelect(guild)}>
      <div className="guild-card-icon">{guild.icon ? <img src={guild.icon} alt="" /> : (guild.name ?? "?").slice(0, 1).toUpperCase()}</div>
      <div className="guild-card-copy"><strong>{guild.name ?? "Server senza nome"}</strong><span><Users size={13} />{guild.active_tickets_count} ticket attivi</span><div><b>{guild.is_admin ? "Admin" : "Staff"}</b>{guild.is_owner && <em>Owner</em>}</div></div><ArrowRight size={18} />
    </button>)}</div>}
  </main>;
}
