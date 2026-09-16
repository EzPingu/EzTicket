import { ChevronDown, ChevronLeft, ChevronRight, ClipboardList, LayoutDashboard, LifeBuoy, PanelLeft, RefreshCw, Search, Sparkles, LogOut } from "lucide-react";
import { useState, type ReactNode } from "react";
import type { GuildDetail, GuildSummary, GuildUserProfile, UserProfile } from "../api/types";
import type { ViewKey } from "../app/App";

const navItems: { label: ViewKey; icon: typeof LayoutDashboard }[] = [
  { label: "Dashboard", icon: LayoutDashboard }, { label: "Ticket", icon: LifeBuoy }, { label: "Candidature", icon: ClipboardList },
];
type Props = { children: ReactNode; activeView: ViewKey; onNavigate: (view: ViewKey) => void; guilds: GuildSummary[]; guild: GuildSummary; detail: GuildDetail; me: GuildUserProfile; user: UserProfile | null; onGuildChange: (id: string) => void; isRefreshing: boolean; onRefresh: () => void; onLogout: () => Promise<void>; toast: string | null; };

export function Layout({ children, activeView, onNavigate, guilds, guild, detail, me, user, onGuildChange, isRefreshing, onRefresh, onLogout, toast }: Props) {
  const [collapsed, setCollapsed] = useState(false);
  const avatarUrl = me.avatar ? `https://cdn.discordapp.com/avatars/${me.id}/${me.avatar}.png?size=64` : undefined;
  const guildIcon = guild.icon ? (guild.icon.startsWith("http") ? guild.icon : `https://cdn.discordapp.com/icons/${guild.id}/${guild.icon}.png?size=64`) : undefined;
  return <div className="app-shell">
    <aside className={`sidebar ${collapsed ? "sidebar--collapsed" : ""}`}>
      <div className="brand"><div className="brand-mark"><Sparkles size={17} /></div>{!collapsed && <span>EzTicket</span>}</div>
      <label className="guild-switcher"><div className="guild-icon">{guildIcon ? <img src={guildIcon} alt="" /> : (guild.name ?? "?").slice(0, 1)}</div>{!collapsed && <select value={guild.id} onChange={(event) => onGuildChange(event.target.value)}><option value={guild.id}>{detail.name ?? guild.name ?? "Server"}</option>{guilds.filter((item) => item.id !== guild.id).map((item) => <option key={item.id} value={item.id}>{item.name ?? "Server senza nome"}</option>)}</select>} {!collapsed && <ChevronDown size={15} className="muted-icon" />}</label>
      <div className="nav-section">{!collapsed && <span className="nav-label">Workspace</span>}{navItems.map(({ label, icon: Icon }) => <button key={label} className={`nav-item ${activeView === label ? "nav-item--active" : ""}`} onClick={() => onNavigate(label)}><Icon size={18} />{!collapsed && <span>{label}</span>}</button>)}</div>
      <div className="sidebar-footer"><button className="profile-card" onClick={() => void onLogout()} title="Logout"><div className="avatar avatar--small">{avatarUrl ? <img src={avatarUrl} alt="" /> : (me.global_name ?? me.username).slice(0, 2).toUpperCase()}<span className="online-dot" /></div>{!collapsed && <div className="profile-copy"><strong>{me.global_name ?? me.username}</strong><span>{me.guild_role}</span></div>}{!collapsed ? <LogOut size={15} className="muted-icon" /> : null}</button><button className="collapse-button" onClick={() => setCollapsed(!collapsed)}>{collapsed ? <ChevronRight size={16} /> : <><ChevronLeft size={16} /><span>Riduci sidebar</span></>}</button></div>
    </aside>
    <main className="main-area"><header className="topbar"><div className="breadcrumbs"><PanelLeft size={16} /><span>EzTicket Manager</span><span className="breadcrumb-separator">/</span><strong>{activeView}</strong></div><div className="topbar-actions"><button className="search-box"><Search size={16} /><span>Cerca...</span><kbd>⌘ K</kbd></button><button className={`icon-button ${isRefreshing ? "is-spinning" : ""}`} onClick={onRefresh} aria-label="Aggiorna"><RefreshCw size={17} /></button><div className="topbar-divider" /><div className="avatar">{me.avatar ? <img src={`https://cdn.discordapp.com/avatars/${me.id}/${me.avatar}.png?size=64`} alt="" /> : (user?.username ?? "U").slice(0, 2).toUpperCase()}<span className="online-dot" /></div></div></header><div className="content">{children}</div></main>{toast && <div className="toast"><span className="toast-check">✓</span>{toast}</div>}</div>;
}
