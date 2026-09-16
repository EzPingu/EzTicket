import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { getCurrentUser, logout as logoutApi } from "../api/auth.api";
import { ApiError, APP_VERSION } from "../api/client";
import { getGuilds } from "../api/guilds.api";
import { getAppVersion } from "../api/version.api";
import type { AuthSession, GuildSummary, UserProfile } from "../api/types";
import { loginWithDiscord } from "./oauth";
import { installAvailableUpdate, type UpdateProgress } from "../updater/update";

type AuthStatus = "loading" | "anonymous" | "authenticated" | "updating" | "update_error";
type AuthContextValue = {
  status: AuthStatus;
  user: UserProfile | null;
  token: string | null;
  expiresAt: number | null;
  guilds: GuildSummary[];
  login: () => Promise<void>;
  logout: () => Promise<void>;
  reloadGuilds: () => Promise<void>;
  expireSession: () => void;
  error: string | null;
  clearError: () => void;
  updateVersion: string | null;
  updateProgress: UpdateProgress | null;
  updateMessage: string;
  retryUpdate: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<UserProfile | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [expiresAt, setExpiresAt] = useState<number | null>(null);
  const [guilds, setGuilds] = useState<GuildSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [updateVersion, setUpdateVersion] = useState<string | null>(null);
  const [updateProgress, setUpdateProgress] = useState<UpdateProgress | null>(null);
  const [updateMessage, setUpdateMessage] = useState("Verifica disponibilità aggiornamenti...");
  const [updateAttempt, setUpdateAttempt] = useState(0);

  const clearSession = useCallback(() => {
    setToken(null);
    setUser(null);
    setExpiresAt(null);
    setGuilds([]);
    setStatus("anonymous");
  }, []);

  const reloadGuilds = useCallback(async () => {
    if (!token) return;
    try {
      setGuilds(await getGuilds(token));
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) clearSession();
      else throw cause;
    }
  }, [clearSession, token]);

  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    setUpdateMessage("Verifica disponibilità aggiornamenti...");
    void getAppVersion()
      .then(async (policy) => {
        if (cancelled) return;
        const mandatory = compareVersions(APP_VERSION, policy.minimum_version) < 0;
        setUpdateVersion(policy.current_version);
        setStatus("updating");
        setUpdateMessage(mandatory ? "Download dell'aggiornamento di sicurezza..." : "Download dell'aggiornamento...");
        const installed = await installAvailableUpdate(setUpdateProgress);
        if (cancelled) return;
        if (installed) {
          setUpdateVersion(installed.version);
          setUpdateMessage("Riavvio...");
          return;
        }
        if (mandatory) {
          throw new Error("È necessario installare un aggiornamento per avviare l'app.");
        }
        setStatus("anonymous");
      })
      .catch((cause) => {
        if (cancelled) return;
        setError(cause instanceof Error ? cause.message : "Aggiornamento non riuscito.");
        setUpdateMessage("Aggiornamento non riuscito.");
        setStatus("update_error");
      });
    return () => { cancelled = true; };
  }, [updateAttempt]);

  const login = useCallback(async () => {
    setError(null);
    try {
      const session: AuthSession = await loginWithDiscord();
      const profile = await getCurrentUser(session.session_token);
      setToken(session.session_token);
      setExpiresAt(session.expires_at);
      setUser(profile);
      setGuilds(await getGuilds(session.session_token));
      setStatus("authenticated");
    } catch (cause) {
      clearSession();
      setError(cause instanceof Error ? cause.message : "Accesso non riuscito.");
      throw cause;
    }
  }, [clearSession]);

  const logout = useCallback(async () => {
    const currentToken = token;
    clearSession();
    if (currentToken) {
      try {
        await logoutApi(currentToken);
      } catch {
        // The local session is intentionally cleared even when the server is offline.
      }
    }
  }, [clearSession, token]);

  const retryUpdate = useCallback(() => {
    setError(null);
    setUpdateProgress(null);
    setUpdateVersion(null);
    setUpdateAttempt((value) => value + 1);
  }, []);
  const value = useMemo(() => ({
    status, user, token, expiresAt, guilds, login, logout, reloadGuilds, expireSession: clearSession, error,
    updateVersion, updateProgress, updateMessage, retryUpdate, clearError: () => setError(null),
  }), [status, user, token, expiresAt, guilds, login, logout, reloadGuilds, error, updateVersion, updateProgress, updateMessage, retryUpdate, clearSession]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

function compareVersions(left: string, right: string) {
  const parse = (value: string) => value.replace(/^v/, "").split(/[.+-]/).slice(0, 3).map((part) => Number(part) || 0);
  const a = parse(left);
  const b = parse(right);
  for (let index = 0; index < 3; index += 1) {
    if (a[index] !== b[index]) return a[index] - b[index];
  }
  return 0;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth deve essere usato dentro AuthProvider.");
  return value;
}
