import type { UpdateInfo } from "electron-updater";

import type { AutoUpdaterChannel, AutoUpdaterEventCallback } from "../electron/autoUpdaterTypes";

type BaseBackendStatusPayload = { message: string };

export type BackendStatusPayloads = {
  loading: BaseBackendStatusPayload;
  running: BaseBackendStatusPayload;
  warning: BaseBackendStatusPayload;
  error: BaseBackendStatusPayload & { message: string; stack: string };
  exited: BaseBackendStatusPayload & { code: number | null; signal: NodeJS.Signals | null; stderr: string };
  unresponsive: BaseBackendStatusPayload;
  shutting_down: BaseBackendStatusPayload;
};

export type BackendStatus<T extends keyof BackendStatusPayloads = keyof BackendStatusPayloads> = {
  status: T;
  payload: BackendStatusPayloads[T];
};

export type AnyBackendStatus = BackendStatus<keyof BackendStatusPayloads>;

// Type definitions for Electron IPC exposed to the renderer
export type SculptorElectronAPI = {
  selectProjectDirectory: () => Promise<string | null>;
  platform: string;
  getCurrentBackendStatus: () => Promise<AnyBackendStatus>;
  onBackendStatusChange: (callback: (state: AnyBackendStatus) => void) => void;
  removeBackendStatusListener: () => void;
  autoUpdater: {
    checkForUpdates: () => Promise<{ updateInfo?: UpdateInfo; error?: string } | null>;
    downloadUpdate: () => Promise<{ success?: boolean; error?: string }>;
    quitAndInstall: () => Promise<void>;
    getCurrentVersion: () => Promise<string>;
    setAutoDownload: (enabled: boolean) => Promise<{ success: boolean }>;
  };
  onAutoUpdaterEvent: (channel: AutoUpdaterChannel, callback: AutoUpdaterEventCallback) => void;
  removeAutoUpdaterListener: (channel: AutoUpdaterChannel, callback: AutoUpdaterEventCallback) => void;
  getSessionToken: () => Promise<string>;
  getBackendPort: () => Promise<number>;
  // Global hotkey management
  setGlobalHotkey: (hotkey: string) => Promise<{ success: boolean; error?: string }>;
  clearGlobalHotkey: () => Promise<{ success: boolean }>;
  // Image storage operations (deprecated, use saveFile/getFileData)
  saveImage: (fileData: ArrayBuffer, filename: string) => Promise<string>;
  getImageData: (filePath: string) => Promise<string>;
  // File storage operations
  saveFile: (fileData: ArrayBuffer, filename: string) => Promise<string>;
  getFileData: (filePath: string) => Promise<string>;
};
