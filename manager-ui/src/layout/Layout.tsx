import {
  Activity,
  BarChart3,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  Clock3,
  FileText,
  LayoutDashboard,
  LifeBuoy,
  PanelLeft,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { ReactNode, useState } from "react";
import { ViewKey } from "../app/App";

const navItems: { label: ViewKey; icon: typeof LayoutDashboard }[] = [
  { label: "Dashboard", icon: LayoutDashboard },
  { label: "Ticket", icon: LifeBuoy },
  { label: "Candidature", icon: ClipboardList },
  { label: "Storico", icon: Clock3 },
  { label: "Statistiche", icon: BarChart3 },
  { label: "Configurazione", icon: Settings2 },
  { label: "Audit", icon: ShieldCheck },
];

type Props = {
  children: ReactNode;
  activeView: ViewKey;
  onNavigate: (view: ViewKey) => void;
  guildId: string;
  guildName: string;
  onGuildChange: (id: string) => void;
  isRefreshing: boolean;
  onRefresh: () => void;
  toast: string | null;
};

export function Layout({
  children,
  activeView,
  onNavigate,
  guildId,
  guildName,
  onGuildChange,
  isRefreshing,
  onRefresh,
  toast,
}: Props) {
  const [collapsed, setCollapsed] = useState(false);
  return (
    <div className="app-shell">
      <aside className={`sidebar ${collapsed ? "sidebar--collapsed" : ""}`}>
        <div className="brand">
          <div className="brand-mark"><Sparkles size={17} /></div>
          {!collapsed && <span>EzTicket</span>}
        </div>

        <button className="guild-switcher" onClick={() => onGuildChange(guildId === "aurora" ? "pixel" : "aurora")}>
          <div className="guild-icon">{guildId === "aurora" ? "A" : "P"}</div>
          {!collapsed && (
            <div className="guild-copy">
              <strong>{guildName}</strong>
              <span>Workspace server</span>
            </div>
          )}
          {!collapsed && <ChevronDown size={15} className="muted-icon" />}
        </button>

        <div className="nav-section">
          {!collapsed && <span className="nav-label">Workspace</span>}
          {navItems.slice(0, 5).map(({ label, icon: Icon }) => (
            <button
              key={label}
              className={`nav-item ${activeView === label ? "nav-item--active" : ""}`}
              onClick={() => onNavigate(label)}
              title={collapsed ? label : undefined}
            >
              <Icon size={18} />
              {!collapsed && <span>{label}</span>}
              {!collapsed && label === "Ticket" && <span className="nav-count">12</span>}
            </button>
          ))}
        </div>
        <div className="nav-section nav-section--bottom">
          {!collapsed && <span className="nav-label">Manage</span>}
          {navItems.slice(5).map(({ label, icon: Icon }) => (
            <button
              key={label}
              className={`nav-item ${activeView === label ? "nav-item--active" : ""}`}
              onClick={() => onNavigate(label)}
              title={collapsed ? label : undefined}
            >
              <Icon size={18} />
              {!collapsed && <span>{label}</span>}
            </button>
          ))}
        </div>

        <div className="sidebar-footer">
          <button className="profile-card" title="Profilo">
            <div className="avatar avatar--small">LM<span className="online-dot" /></div>
            {!collapsed && <div className="profile-copy"><strong>Leo Martini</strong><span>Administrator</span></div>}
            {!collapsed && <ChevronRight size={15} className="muted-icon" />}
          </button>
          <button className="collapse-button" onClick={() => setCollapsed(!collapsed)}>
            {collapsed ? <ChevronRight size={16} /> : <><ChevronLeft size={16} /><span>Riduci sidebar</span></>}
          </button>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="breadcrumbs"><PanelLeft size={16} /><span>EzTicket Manager</span><span className="breadcrumb-separator">/</span><strong>{activeView}</strong></div>
          <div className="topbar-actions">
            <button className="search-box"><Search size={16} /><span>Cerca...</span><kbd>⌘ K</kbd></button>
            <button className={`icon-button ${isRefreshing ? "is-spinning" : ""}`} onClick={onRefresh} aria-label="Aggiorna"><RefreshCw size={17} /></button>
            <div className="topbar-divider" />
            <div className="avatar">LM<span className="online-dot" /></div>
          </div>
        </header>
        <div className="content">{children}</div>
      </main>
      {toast && <div className="toast"><span className="toast-check">✓</span>{toast}</div>}
    </div>
  );
}
