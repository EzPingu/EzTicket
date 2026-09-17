```ts
import { check, type Update } from "@tauri-apps/plugin-updater";
import { isTauri } from "@tauri-apps/api/core";
import { relaunch } from "@tauri-apps/plugin-process";

export type UpdateProgress = {
  downloaded: number;
  total: number | null;
};

export async function installAvailableUpdate(
  onProgress: (progress: UpdateProgress) => void,
): Promise<{ version: string } | null> {
  if (!isTauri()) {
    console.log("[EzTicket Updater] Non eseguito: ambiente non-Tauri.");
    return null;
  }

  console.log("[EzTicket Updater] Avvio controllo aggiornamenti...");

  let update: Update | null;

  try {
    update = await check();
    console.log(
      "[EzTicket Updater] Risultato check():",
      update
        ? {
            version: update.version,
            currentVersion: update.currentVersion,
            date: update.date,
            body: update.body,
          }
        : "Nessun aggiornamento disponibile",
    );
  } catch (error) {
    console.error("[EzTicket Updater] ERRORE durante check():", error);
    console.error(
      "[EzTicket Updater] Errore dettagliato:",
      error instanceof Error
        ? {
            name: error.name,
            message: error.message,
            stack: error.stack,
          }
        : error,
    );
    throw error;
  }

  if (!update) {
    console.log("[EzTicket Updater] App già aggiornata.");
    return null;
  }

  console.log(
    `[EzTicket Updater] Aggiornamento trovato: ${update.version}`,
  );

  let downloaded = 0;
  let total: number | null = null;

  try {
    await update.downloadAndInstall((event) => {
      console.log("[EzTicket Updater] Evento download:", event);

      if (event.event === "Started") {
        total = event.data.contentLength ?? null;

        console.log(
          "[EzTicket Updater] Download iniziato. Dimensione:",
          total,
        );

        onProgress({
          downloaded: 0,
          total,
        });
      } else if (event.event === "Progress") {
        downloaded += event.data.chunkLength;

        console.log(
          `[EzTicket Updater] Download: ${downloaded}${
            total !== null ? ` / ${total}` : ""
          } bytes`,
        );

        onProgress({
          downloaded,
          total,
        });
      } else if (event.event === "Finished") {
        console.log(
          "[EzTicket Updater] Download e installazione terminati.",
        );

        onProgress({
          downloaded,
          total: downloaded,
        });
      }
    });
  } catch (error) {
    console.error(
      "[EzTicket Updater] ERRORE durante downloadAndInstall():",
      error,
    );

    console.error(
      "[EzTicket Updater] Errore dettagliato:",
      error instanceof Error
        ? {
            name: error.name,
            message: error.message,
            stack: error.stack,
          }
        : error,
    );

    throw error;
  }

  console.log("[EzTicket Updater] Riavvio dell'app...");

  try {
    await relaunch();
  } catch (error) {
    console.error("[EzTicket Updater] ERRORE durante relaunch():", error);

    console.error(
      "[EzTicket Updater] Errore dettagliato:",
      error instanceof Error
        ? {
            name: error.name,
            message: error.message,
            stack: error.stack,
          }
        : error,
    );

    throw error;
  }

  return {
    version: update.version,
  };
}
```
