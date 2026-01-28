import type { SculptorElectronAPI } from "./shared/types.ts";

declare global {
  // eslint-disable-next-line @typescript-eslint/consistent-type-definitions
  interface Window {
    sculptor?: SculptorElectronAPI;
  }

  declare const API_URL_BASE: string | undefined;
}
