import { check, type Update } from "@tauri-apps/plugin-updater";
import { isTauri } from "@tauri-apps/api/core";
import { relaunch } from "@tauri-apps/plugin-process";

export type UpdateProgress = {
  downloaded: number;
  total: number | null;
};

function formatError(error: unknown): string {
  if (error instanceof Error) {
    return `${error.name}: ${error.message}${error.stack ? `\n\nStack:\n${error.stack}` : ""}`;
  }

  if (typeof error === "string") {
    return error;
  }

  try {
    return JSON.stringify(error, null, 2);
  } catch {
    return String(error);
  }
}

export async function installAvailableUpdate(
  onProgress: (progress: UpdateProgress) => void,
): Promise<{ version: string } | null> {
  if (!isTauri()) {
    return null;
  }

  let update: Update | null;

  try {
    update = await check();
  } catch (error) {
    const details = formatError(error);

    console.error("[EzTicket Updater] check() failed:", details);

    throw new Error(
      `ERRORE CHECK AGGIORNAMENTO\n\n${details}`,
    );
  }

  if (!update) {
    return null;
  }

  let downloaded = 0;
  let total: number | null = null;

  try {
    await update.downloadAndInstall((event) => {
      if (event.event === "Started") {
        total = event.data.contentLength ?? null;

        onProgress({
          downloaded: 0,
          total,
        });
      } else if (event.event === "Progress") {
        downloaded += event.data.chunkLength;

        onProgress({
          downloaded,
          total,
        });
      } else if (event.event === "Finished") {
        onProgress({
          downloaded,
          total: downloaded,
        });
      }
    });
  } catch (error) {
    const details = formatError(error);

    console.error(
      "[EzTicket Updater] downloadAndInstall() failed:",
      details,
    );

    throw new Error(
      `ERRORE DOWNLOAD/INSTALLAZIONE\n\n${details}`,
    );
  }

  try {
    await relaunch();
  } catch (error) {
    const details = formatError(error);

    console.error("[EzTicket Updater] relaunch() failed:", details);

    throw new Error(
      `ERRORE RIAVVIO APP\n\n${details}`,
    );
  }

  return {
    version: update.version,
  };
}

