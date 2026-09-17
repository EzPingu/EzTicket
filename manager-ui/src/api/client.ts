export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000/api/v1").replace(/\/$/, "");
import packageJson from "../../package.json";

export const APP_VERSION = packageJson.version;

export class ApiError extends Error {
  status: number;
  code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export async function apiRequest<T>(
  path: string,
  options: RequestInit = {},
  token?: string,
): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 15000);
  const headers = new Headers(options.headers);
  if (options.body) headers.set("Content-Type", "application/json");
  headers.set("X-Manager-Version", APP_VERSION);
  if (token) headers.set("Authorization", "Bearer " + token);
  const url = `${API_BASE_URL}${path}`;
  const isVersionCheck = path === "/app/version";

  try {
    if (isVersionCheck) {
      console.info("[VersionCheck] richiesta", { url, appVersion: APP_VERSION });
    }
    const response = await fetch(url, {
      ...options,
      headers,
      signal: controller.signal,
    });
    const body = await response.json().catch(() => null);
    if (isVersionCheck) {
      console.info("[VersionCheck] risposta", {
        url,
        status: response.status,
        ok: response.ok,
        body,
      });
    }
    if (!response.ok) {
      const message =
        typeof body?.detail === "string"
          ? body.detail
          : typeof body?.error === "string"
            ? body.error
            : "La richiesta non è riuscita.";
      throw new ApiError(message, response.status, body?.code);
    }
    return body as T;
  } catch (error) {
    if (isVersionCheck) {
      console.error("[VersionCheck] errore", {
        url,
        error: error instanceof Error ? error.message : String(error),
        name: error instanceof Error ? error.name : undefined,
      });
    }
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError("Il server ha impiegato troppo tempo a rispondere.", 408);
    }
    throw new ApiError("Impossibile raggiungere il server. Controlla la connessione e riprova.", 0);
  } finally {
    window.clearTimeout(timeout);
  }
}
