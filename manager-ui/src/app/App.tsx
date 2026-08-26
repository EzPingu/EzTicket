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

export type ViewKey = "Dashboard" | "Ticket" | "Candidature" | "Storico" | "Statistiche" | "Configurazione" | "Audit";

function AuthenticatedApp() {
  const { status, token, user, guilds, reloadGuilds, logout, expireSession } = useAuth();
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
  if (status === "anonymous") return <LoginScreen />;
  if (selectionLoading) return <div className="full-state"><span className="button-spinner" />Caricamento del server...</div>;
  if (!selectedGuild || !guildDetail || !guildMe) {
    return <ServerSelectionScreen guilds={guilds} loading={false} error={selectionError} user={user} onSelect={selectGuild} onRetry={() => void reloadGuilds().catch((cause) => setSelectionError(formatError(cause)))} onLogout={logout} />;
  }
  return <Layout activeView={activeView} onNavigate={setActiveView} guilds={guilds} guild={selectedGuild} detail={guildDetail} me={guildMe} user={user} onGuildChange={changeGuild} isRefreshing={refreshing} onRefresh={refresh} onLogout={logout} toast={toast}>
    {activeView === "Dashboard" ? <Dashboard token={token!} guildId={selectedGuild.id} guildName={guildDetail.name ?? selectedGuild.name ?? "Server"} user={guildMe} isRefreshing={refreshing} onUnauthorized={expireSession} /> : activeView === "Ticket" ? <TicketWorkspace token={token!} guildId={selectedGuild.id} isRefreshing={refreshing} onUnauthorized={expireSession} /> : <section className="placeholder-view"><span className="eyebrow">Workspace</span><h1>{activeView}</h1><p>Questa area sarà collegata in una milestone successiva.</p></section>}
  </Layout>;
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
