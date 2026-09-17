import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { getCurrentUser, logout as logoutApi } from "../api/auth.api";
import { ApiError } from "../api/client";
import { getGuilds } from "../api/guilds.api";
import type { AuthSession, GuildSummary, UserProfile } from "../api/types";
import { loginWithDiscord } from "./oauth";
import { checkManagerVersion } from "../versionCheck";

type AuthStatus = "loading" | "anonymous" | "authenticated" | "version_blocked" | "version_unavailable";
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
  minimumVersion: string | null;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<UserProfile | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [expiresAt, setExpiresAt] = useState<number | null>(null);
  const [guilds, setGuilds] = useState<GuildSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [minimumVersion, setMinimumVersion] = useState<string | null>(null);

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
    void checkManagerVersion().then((result) => {
      if (cancelled) return;
      if (result.state === "blocked") {
        setMinimumVersion(result.policy.minimum_version);
        // Discord OAuth is required so the backend can send the mandatory-update DM.
        setStatus("anonymous");
      } else if (result.state === "unavailable") {
        setError(result.error.message);
        setStatus("version_unavailable");
      } else {
        setStatus("anonymous");
      }
    });
    return () => { cancelled = true; };
  }, []);

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
      if (cause instanceof ApiError && cause.status === 426) {
        setStatus("version_blocked");
        setError(null);
        return;
      }
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

  const value = useMemo(() => ({
    status, user, token, expiresAt, guilds, login, logout, reloadGuilds, expireSession: clearSession, error,
    minimumVersion, clearError: () => setError(null),
  }), [status, user, token, expiresAt, guilds, login, logout, reloadGuilds, error, minimumVersion, clearSession]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth deve essere usato dentro AuthProvider.");
  return value;
}
