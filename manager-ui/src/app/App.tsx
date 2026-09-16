import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../api/client";
import { getGuildDetail, getGuildMe } from "../api/guilds.api";
import type { GuildDetail, GuildSummary, GuildUserProfile } from "../api/types";
import { AuthProvider, useAuth } from "../auth/AuthContext";
import { LoginScreen } from "../auth/LoginScreen";
import { ServerSelectionScreen } from "../features/guilds/ServerSelectionScreen";
import { Layout } from "../layout/Layout";
import { Dashboard } from "../features/dashboard/Dashboard";
import { TicketWorkspace } from "../features/tickets/TicketWorkspace";
import { ApplicationsWorkspace } from "../features/applications/ApplicationsWorkspace";

export type ViewKey = "Dashboard" | "Ticket" | "Candidature" | "Storico" | "Statistiche" | "Configurazione" | "Audit";

function AuthenticatedApp() {
  const { status, token, user, guilds, reloadGuilds, logout, expireSession, updateVersion, updateProgress, updateMessage, retryUpdate } = useAuth();
  const [selectedGuild, setSelectedGuild] = useState<GuildSummary | null>(null);
  const [guildDetail, setGuildDetail] = useState<GuildDetail | null>(null);
  const [guildMe, setGuildMe] = useState<GuildUserProfile | null>(null);
  const [selectionLoading, setSelectionLoading] = useState(false);
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const [activeView, setActiveView] = useState<ViewKey>("Dashboard");
  const [refreshing, setRefreshing] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    setSelectedGuild(null);
    setGuildDetail(null);
    setGuildMe(null);
  }, [token]);

  const selectGuild = useCallback(async (guild: GuildSummary) => {
    if (!token) return;
    setSelectionLoading(true);
    setSelectionError(null);
    setSelectedGuild(null);
    setGuildDetail(null);
    setGuildMe(null);
    try {
      const [detail, me] = await Promise.all([getGuildDetail(token, guild.id), getGuildMe(token, guild.id)]);
      setSelectedGuild(guild);
      setGuildDetail(detail);
      setGuildMe(me);
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) { expireSession(); return; }
      setSelectionError(formatError(cause));
    } finally {
      setSelectionLoading(false);
    }
  }, [expireSession, token]);

  const changeGuild = (id: string) => {
    const guild = guilds.find((item) => item.id === id);
    if (guild) void selectGuild(guild);
  };

  const refresh = () => {
    setRefreshing(true);
    void reloadGuilds().finally(() => {
      setRefreshing(false);
      setToast("Server aggiornati");
      window.setTimeout(() => setToast(null), 2200);
    });
  };

  if (status === "loading") return <div className="full-state"><span className="button-spinner" />Avvio di EzTicket Manager...</div>;
  if (status === "updating" || status === "update_error") {
    return <UpdateScreen status={status} version={updateVersion} progress={updateProgress} message={updateMessage} onRetry={retryUpdate} />;
  }
  if (status === "anonymous") return <LoginScreen />;
  if (selectionLoading) return <div className="full-state"><span className="button-spinner" />Caricamento del server...</div>;
  if (!selectedGuild || !guildDetail || !guildMe) {
    return <ServerSelectionScreen guilds={guilds} loading={false} error={selectionError} user={user} onSelect={selectGuild} onRetry={() => void reloadGuilds().catch((cause) => setSelectionError(formatError(cause)))} onLogout={logout} />;
  }

  return <Layout activeView={activeView} onNavigate={setActiveView} guilds={guilds} guild={selectedGuild} detail={guildDetail} me={guildMe} user={user} onGuildChange={changeGuild} isRefreshing={refreshing} onRefresh={refresh} onLogout={logout} toast={toast}>
    {activeView === "Dashboard" ? <Dashboard token={token!} guildId={selectedGuild.id} guildName={guildDetail.name ?? selectedGuild.name ?? "Server"} user={guildMe} isRefreshing={refreshing} onUnauthorized={expireSession} /> : activeView === "Ticket" ? <TicketWorkspace token={token!} guildId={selectedGuild.id} isRefreshing={refreshing} onUnauthorized={expireSession} /> : activeView === "Candidature" ? <ApplicationsWorkspace token={token!} guildId={selectedGuild.id} isRefreshing={refreshing} onUnauthorized={expireSession} /> : <section className="placeholder-view"><span className="eyebrow">Workspace</span><h1>{activeView}</h1><p>Questa area sarà collegata in una milestone successiva.</p></section>}
  </Layout>;
}

function UpdateScreen({
  status,
  version,
  progress,
  message,
  onRetry,
}: {
  status: "updating" | "update_error";
  version: string | null;
  progress: { downloaded: number; total: number | null } | null;
  message: string;
  onRetry: () => void;
}) {
  const percentage = progress?.total ? Math.min(100, Math.round((progress.downloaded / progress.total) * 100)) : null;
  return <main className="update-screen" role="dialog" aria-modal="true">
    <section className="update-card">
      <span className="update-icon">↻</span>
      <h1>{status === "update_error" ? "Aggiornamento non riuscito" : "Aggiornamento di EzTicket Manager"}</h1>
      <p>L'app si sta aggiornando alla versione <strong>v{version ?? "disponibile"}</strong></p>
      <p className="update-subtitle">Senza questo aggiornamento l'app non può essere avviata per motivi di sicurezza.</p>
      {status === "updating" && <div className="update-progress" aria-label="Progresso aggiornamento">
        <div className="update-progress-bar" style={{ width: `${percentage ?? 35}%` }} />
      </div>}
      <span className="update-status">{message}</span>
      {status === "update_error" && <button className="secondary-button" onClick={onRetry}>Riprova</button>}
    </section>
  </main>;
}

function formatError(cause: unknown) {
  if (cause instanceof ApiError) {
    if (cause.status === 403) return "Non hai i permessi necessari per gestire questo server.";
    if (cause.status === 404) return "Il server richiesto non è più disponibile.";
    return cause.message;
  }
  return cause instanceof Error ? cause.message : "Si è verificato un errore. Riprova.";
}

export function App() {
  return <AuthProvider><AuthenticatedApp /></AuthProvider>;
}
