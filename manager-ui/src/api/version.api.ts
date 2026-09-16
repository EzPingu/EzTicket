import { apiRequest } from "./client";

export type AppVersion = {
  current_version: string;
  minimum_version: string;
  download_url: string;
};

export function getAppVersion() {
  return apiRequest<AppVersion>("/app/version");
}
