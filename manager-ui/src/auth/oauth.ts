import { invoke, isTauri } from "@tauri-apps/api/core";
import { openUrl } from "@tauri-apps/plugin-opener";
import { exchangeOAuthCode } from "../api/auth.api";

const clientId = import.meta.env.VITE_DISCORD_CLIENT_ID as string | undefined;
const redirectUri = "http://127.0.0.1:8765/callback";

function randomBase64Url(bytes: number) {
  const data = new Uint8Array(bytes);
  crypto.getRandomValues(data);
  return btoa(String.fromCharCode(...data)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function challenge(verifier: string) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return btoa(String.fromCharCode(...new Uint8Array(digest))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function safeOpenUrlError(cause: unknown) {
  const detail = cause instanceof Error ? cause.message : String(cause);
  return detail.replace(/https?:\/\/\S+/gi, "[URL OAuth omesso]");
}

export async function loginWithDiscord() {
  if (!clientId) throw new Error("Discord OAuth non configurato.");
  if (!isTauri()) {
    throw new Error("Il login Discord è disponibile solo nell'app desktop EzTicket Manager.");
  }

  const verifier = randomBase64Url(64);
  const state = randomBase64Url(32);
  const codeChallenge = await challenge(verifier);
  await invoke("start_oauth_callback");
  const params = new URLSearchParams({
    client_id: clientId,
    response_type: "code",
    redirect_uri: redirectUri,
    scope: "identify guilds",
    state,
    code_challenge: codeChallenge,
    code_challenge_method: "S256",
  });
  const authorizationUrl = `https://discord.com/oauth2/authorize?${params.toString()}`;
  try {
    await openUrl(authorizationUrl);
  } catch (cause) {
    const detail = safeOpenUrlError(cause);
    console.error("Discord OAuth: openUrl() non ha aperto il browser.", detail);
    throw new Error(`Impossibile aprire il browser per Discord OAuth. ${detail}`);
  }
  const callback = await invoke<{ code?: string; state?: string; error?: string }>("wait_oauth_callback");
  if (callback.error) throw new Error("Discord ha annullato l'autenticazione.");
  if (!callback.code || callback.state !== state) throw new Error("La verifica di sicurezza OAuth non è riuscita.");
  return exchangeOAuthCode({ code: callback.code, code_verifier: verifier, redirect_uri: redirectUri });
}
