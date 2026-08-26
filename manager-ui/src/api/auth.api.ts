import { apiRequest } from "./client";
import type { AuthSession, UserProfile } from "./types";

export function exchangeOAuthCode(payload: {
  code: string;
  code_verifier: string;
  redirect_uri: string;
}) {
  return apiRequest<AuthSession>("/auth/exchange", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getCurrentUser(token: string) {
  return apiRequest<UserProfile>("/auth/me", {}, token);
}

export function logout(token: string) {
  return apiRequest<{ success: boolean; message: string }>("/auth/logout", { method: "POST" }, token);
}
