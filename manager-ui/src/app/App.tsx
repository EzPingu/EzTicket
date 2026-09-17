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
  const { status, token, user, guilds, reloadGuilds, logout, expireSession, minimumVersion, error } = useAuth();
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
  if (status === "version_blocked") return <VersionBlockedScreen minimumVersion={minimumVersion} />;
  if (status === "version_unavailable") return <VersionUnavailableScreen message={error} />;
  if (status === "anonymous") return <LoginScreen />;
  if (selectionLoading) return <div className="full-state"><span className="button-spinner" />Caricamento del server...</div>;
  if (!selectedGuild || !guildDetail || !guildMe) {
    return <ServerSelectionScreen guilds={guilds} loading={false} error={selectionError} user={user} onSelect={selectGuild} onRetry={() => void reloadGuilds().catch((cause) => setSelectionError(formatError(cause)))} onLogout={logout} />;
  }

  return <Layout activeView={activeView} onNavigate={setActiveView} guilds={guilds} guild={selectedGuild} detail={guildDetail} me={guildMe} user={user} onGuildChange={changeGuild} isRefreshing={refreshing} onRefresh={refresh} onLogout={logout} toast={toast}>
    {activeView === "Dashboard" ? <Dashboard token={token!} guildId={selectedGuild.id} guildName={guildDetail.name ?? selectedGuild.name ?? "Server"} user={guildMe} isRefreshing={refreshing} onUnauthorized={expireSession} /> : activeView === "Ticket" ? <TicketWorkspace token={token!} guildId={selectedGuild.id} isRefreshing={refreshing} onUnauthorized={expireSession} /> : activeView === "Candidature" ? <ApplicationsWorkspace token={token!} guildId={selectedGuild.id} isRefreshing={refreshing} onUnauthorized={expireSession} /> : <section className="placeholder-view"><span className="eyebrow">Workspace</span><h1>{activeView}</h1><p>Questa area sarà collegata in una milestone successiva.</p></section>}
  </Layout>;
}

function VersionBlockedScreen({ minimumVersion }: { minimumVersion: string | null }) {
  return <main className="update-screen" role="dialog" aria-modal="true">
    <section className="update-card">
      <span className="update-icon">⚠</span>
      <h1>Aggiornamento necessario</h1>
      <p>È disponibile un aggiornamento obbligatorio per continuare ad usare EzTicket Manager.</p>
      <p className="update-subtitle">Controlla i tuoi DM Discord per ricevere il link e le istruzioni per aggiornare.</p>
      {minimumVersion && <span className="update-status">Versione minima richiesta: v{minimumVersion}</span>}
    </section>
  </main>;
}

function VersionUnavailableScreen({ message }: { message: string | null }) {
  return <main className="update-screen" role="alert" aria-live="assertive">
    <section className="update-card">
      <span className="update-icon">⚠</span>
      <h1>Impossibile verificare la versione</h1>
      <p>Controlla la connessione e riprova.</p>
      {message && <p className="update-subtitle">{message}</p>}
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
