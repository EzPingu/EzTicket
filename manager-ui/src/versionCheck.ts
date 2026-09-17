import { getAppVersion, type AppVersion } from "./api/version.api";
import { APP_VERSION } from "./api/client";

export type VersionCheckResult =
  | { state: "supported"; policy: AppVersion }
  | { state: "blocked"; policy: AppVersion }
  | { state: "unavailable"; error: Error };

export async function checkManagerVersion(): Promise<VersionCheckResult> {
  try {
    const policy = await getAppVersion();
    return {
      state: compareVersions(APP_VERSION, policy.minimum_version) < 0 ? "blocked" : "supported",
      policy,
    };
  } catch (cause) {
    return {
      state: "unavailable",
      error: cause instanceof Error
        ? cause
        : new Error("Impossibile verificare la versione. Controlla la connessione e riprova."),
    };
  }
}

export function compareVersions(left: string, right: string): number {
  const parse = (value: string) =>
    value.replace(/^v/, "").split(/[.+-]/).slice(0, 3).map((part) => Number(part) || 0);
  const a = parse(left);
  const b = parse(right);
  for (let index = 0; index < 3; index += 1) {
    if (a[index] !== b[index]) return a[index] - b[index];
  }
  return 0;
}
