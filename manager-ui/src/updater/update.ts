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
  if (!isTauri()) return null;
  const update: Update | null = await check();
  if (!update) return null;

  let downloaded = 0;
  let total: number | null = null;
  await update.downloadAndInstall((event) => {
    if (event.event === "Started") {
      total = event.data.contentLength ?? null;
      onProgress({ downloaded: 0, total });
    } else if (event.event === "Progress") {
      downloaded += event.data.chunkLength;
      onProgress({ downloaded, total });
    } else if (event.event === "Finished") {
      onProgress({ downloaded, total: downloaded });
    }
  });
  await relaunch();
  return { version: update.version };
}
