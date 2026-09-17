import { getAppVersion, type AppVersion } from "./api/version.api";
import { API_BASE_URL, APP_VERSION } from "./api/client";

export type VersionCheckResult =
  | { state: "supported"; policy: AppVersion }
  | { state: "blocked"; policy: AppVersion }
  | { state: "unavailable"; error: Error };

export async function checkManagerVersion(): Promise<VersionCheckResult> {
  console.info("[VersionCheck] avvio", {
    url: `${API_BASE_URL}/app/version`,
    appVersion: APP_VERSION,
  });
  try {
    const policy = await getAppVersion();
    if (!isAppVersion(policy)) {
      throw new Error("La risposta del server contiene una policy di versione non valida.");
    }
    const state = compareVersions(APP_VERSION, policy.minimum_version) < 0 ? "blocked" : "supported";
    console.info("[VersionCheck] policy interpretata", {
      appVersion: APP_VERSION,
      minimumVersion: policy.minimum_version,
      state,
    });
    return {
      state,
      policy,
    };
  } catch (cause) {
    console.error("[VersionCheck] policy non disponibile", {
      url: `${API_BASE_URL}/app/version`,
      error: cause instanceof Error ? cause.message : String(cause),
    });
    return {
      state: "unavailable",
      error: cause instanceof Error
        ? cause
        : new Error("Impossibile verificare la versione. Controlla la connessione e riprova."),
    };
  }
}

function isAppVersion(value: unknown): value is AppVersion {
  if (!value || typeof value !== "object") return false;
  const policy = value as Record<string, unknown>;
  return (
    typeof policy.current_version === "string" &&
    policy.current_version.trim().length > 0 &&
    typeof policy.minimum_version === "string" &&
    policy.minimum_version.trim().length > 0 &&
    typeof policy.download_url === "string"
  );
}

export function compareVersions(left: string, right: string): number {
  const parse = (value: string) =>
    value.trim().replace(/^v/i, "").split(/[.+-]/).slice(0, 3).map((part) => Number(part) || 0);
  const a = parse(left);
  const b = parse(right);
  for (let index = 0; index < 3; index += 1) {
    if (a[index] !== b[index]) return a[index] - b[index];
  }
  return 0;
}
