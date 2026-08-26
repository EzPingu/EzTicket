import { useMemo, useState } from "react";
import { Layout } from "../layout/Layout";
import { Dashboard } from "../features/dashboard/Dashboard";

export type ViewKey =
  | "Dashboard"
  | "Ticket"
  | "Candidature"
  | "Storico"
  | "Statistiche"
  | "Configurazione"
  | "Audit";

export function App() {
  const [activeView, setActiveView] = useState<ViewKey>("Dashboard");
  const [guildId, setGuildId] = useState("aurora");
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const guildName = useMemo(
    () => (guildId === "aurora" ? "Aurora Community" : "Pixel District"),
    [guildId],
  );

  const refresh = () => {
    setIsRefreshing(true);
    window.setTimeout(() => {
      setIsRefreshing(false);
      setToast("Dati aggiornati");
      window.setTimeout(() => setToast(null), 2600);
    }, 650);
  };

  return (
    <Layout
      activeView={activeView}
      onNavigate={setActiveView}
      guildId={guildId}
      guildName={guildName}
      onGuildChange={setGuildId}
      isRefreshing={isRefreshing}
      onRefresh={refresh}
      toast={toast}
    >
      {activeView === "Dashboard" ? (
        <Dashboard guildName={guildName} isRefreshing={isRefreshing} />
      ) : (
        <section className="placeholder-view">
          <span className="eyebrow">Workspace</span>
          <h1>{activeView}</h1>
          <p>
            Questa area e pronta per il collegamento ai dati del Manager
            backend.
          </p>
        </section>
      )}
    </Layout>
  );
}
