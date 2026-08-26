import { ArrowRight, CheckCircle2, Hash, LogOut, Users } from "lucide-react";
import type { GuildSummary, UserProfile } from "../../api/types";

type Props = { guilds: GuildSummary[]; loading: boolean; error: string | null; user: UserProfile | null; onSelect: (guild: GuildSummary) => void; onRetry: () => void; onLogout: () => Promise<void>; };
export function ServerSelectionScreen({ guilds, loading, error, user, onSelect, onRetry, onLogout }: Props) {
  const density = guilds.length <= 3 ? "spacious" : guilds.length <= 10 ? "balanced" : "compact";
  const avatarUrl = user?.avatar ? `https://cdn.discordapp.com/avatars/${user.id}/${user.avatar}.png?size=128` : undefined;

  return <main className={`server-screen server-screen--${density}`}>
    <div className="server-profile">
      <div className="server-profile-identity">
        <div className="server-profile-avatar">{avatarUrl ? <img src={avatarUrl} alt="" /> : (user?.global_name ?? user?.username ?? "U").slice(0, 2).toUpperCase()}</div>
        <div className="server-profile-copy">
          <strong>{user?.global_name ?? user?.username ?? "Utente Discord"}</strong>
          <span>@{user?.username ?? "utente"}</span>
          <small><Hash size={11} />{user?.id ?? "ID non disponibile"}</small>
        </div>
      </div>
      <button className="server-logout" onClick={() => void onLogout()}><LogOut size={14} />Logout</button>
    </div>

    <div className="server-heading"><span className="eyebrow">EzTicket Manager <span className="server-heading-dot" /></span><h1>Scegli il tuo server</h1><p>Gestisci ticket, staff e configurazione del server Discord in un unico spazio.</p></div>
    {loading && <div className="state-card"><span className="button-spinner" /><span>Caricamento dei tuoi server...</span></div>}
    {error && <div className="state-card state-card--error"><strong>Non riesco a caricare i server</strong><span>{error}</span><button className="secondary-button" onClick={onRetry}>Riprova</button></div>}
    {!loading && !error && guilds.length === 0 && <div className="state-card"><strong>Nessun server disponibile</strong><span>Non hai ancora accesso a un server gestibile.</span></div>}
    {!loading && !error && guilds.length > 0 && <div className="guild-grid">{guilds.map((guild) => <button className="guild-card" key={guild.id} onClick={() => onSelect(guild)}>
      <div className="guild-card-icon">{guild.icon ? <img src={guild.icon.startsWith("http") ? guild.icon : `https://cdn.discordapp.com/icons/${guild.id}/${guild.icon}.png?size=128`} alt="" /> : (guild.name ?? "?").slice(0, 1).toUpperCase()}</div>
      <div className="guild-card-copy"><strong>{guild.name ?? "Server senza nome"}</strong><span className="guild-card-members"><Users size={13} />{guild.member_count == null ? "Membri non disponibili" : `${guild.member_count.toLocaleString("it-IT")} membri`}</span><span className="guild-card-status"><CheckCircle2 size={14} />Bot EzTicket presente</span><div><b>{guild.is_admin ? "Admin" : "Staff"}</b>{guild.is_owner && <em>Owner</em>}<span className="guild-card-tickets"><Users size={12} />{guild.active_tickets_count} attivi</span></div></div><span className="guild-card-action"><ArrowRight size={18} /></span>
    </button>)}</div>}
  </main>;
}
