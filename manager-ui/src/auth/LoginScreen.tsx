import { Sparkles } from "lucide-react";
import { useState } from "react";
import { useAuth } from "./AuthContext";

export function LoginScreen() {
  const { login, error, clearError } = useAuth();
  const [loading, setLoading] = useState(false);
  const handleLogin = async () => {
    clearError();
    setLoading(true);
    try { await login(); } catch { /* error is surfaced by AuthContext */ } finally { setLoading(false); }
  };
  return (
    <main className="auth-screen">
      <div className="auth-glow" />
      <section className="auth-card">
        <div className="brand auth-brand"><div className="brand-mark"><Sparkles size={20} /></div><span>EzTicket</span></div>
        <span className="eyebrow">Manager workspace</span>
        <h1>EzTicket Manager</h1>
        <p>Gestisci i tuoi server Discord con una visione chiara, veloce e professionale.</p>
        <button className="discord-button" onClick={handleLogin} disabled={loading}>
          {loading ? <span className="button-spinner" /> : <span className="discord-mark">◉</span>}
          {loading ? "Connessione a Discord..." : "Accedi con Discord"}
        </button>
        {error && <div className="auth-error"><strong>Accesso non riuscito</strong><span>{error}</span><button onClick={handleLogin}>Riprova</button></div>}
      </section>
    </main>
  );
}
