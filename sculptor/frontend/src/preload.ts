import type { IpcRendererEvent } from "electron";
import { contextBridge, ipcRenderer } from "electron";

import type { AutoUpdaterChannel } from "./electron/autoUpdaterTypes";
import {
  BACKEND_PORT_CHANNEL_NAME,
  BACKEND_STATUS_CHANGE_CHANNEL_NAME,
  CLEAR_GLOBAL_HOTKEY_CHANNEL_NAME,
  GET_CURRENT_BACKEND_STATUS_CHANNEL_NAME,
  GET_FILE_DATA_CHANNEL_NAME,
  GET_IMAGE_DATA_CHANNEL_NAME,
  SAVE_FILE_CHANNEL_NAME,
  SAVE_IMAGE_CHANNEL_NAME,
  SELECT_PROJECT_DIRECTORY_CHANNEL_NAME,
  SET_GLOBAL_HOTKEY_CHANNEL_NAME,
} from "./electron/constants.ts";
import type { AnyBackendStatus } from "./shared/types.ts";

type IpcCallback = (event: IpcRendererEvent, data?: unknown) => void;

contextBridge.exposeInMainWorld("sculptor", {
  platform: process.platform,
  // Select a project directory using native file dialog
  selectProjectDirectory: () => ipcRenderer.invoke(SELECT_PROJECT_DIRECTORY_CHANNEL_NAME),
  // Get current backend process state
  getCurrentBackendStatus: () => ipcRenderer.invoke(GET_CURRENT_BACKEND_STATUS_CHANNEL_NAME),
  // Register callback for backend process state updates
  onBackendStatusChange: (callback: (state: AnyBackendStatus) => void) =>
    ipcRenderer.on(BACKEND_STATUS_CHANGE_CHANNEL_NAME, (_event, state) => callback(state)),
  // Remove backend state listener
  removeBackendStatusListener: () => ipcRenderer.removeAllListeners(BACKEND_STATUS_CHANGE_CHANNEL_NAME),
  // Auto-updater event listeners
  onAutoUpdaterEvent: (channel: AutoUpdaterChannel, callback: IpcCallback) => {
    // No validation needed - TypeScript ensures only valid enum values can be passed
    ipcRenderer.on(channel, callback);
  },
  removeAutoUpdaterListener: (channel: AutoUpdaterChannel, callback: IpcCallback) => {
    ipcRenderer.removeListener(channel, callback);
  },
  // Auto-updater commands
  autoUpdater: {
    checkForUpdates: () => ipcRenderer.invoke("auto-updater-check-for-updates"),
    downloadUpdate: () => ipcRenderer.invoke("auto-updater-download-update"),
    quitAndInstall: () => ipcRenderer.invoke("auto-updater-quit-and-install"),
    getCurrentVersion: () => ipcRenderer.invoke("auto-updater-get-current-version"),
    setAutoDownload: (enabled: boolean) => ipcRenderer.invoke("auto-updater-set-auto-download", enabled),
  },
  // Global hotkey management
  setGlobalHotkey: (hotkey: string) => ipcRenderer.invoke(SET_GLOBAL_HOTKEY_CHANNEL_NAME, hotkey),
  clearGlobalHotkey: () => ipcRenderer.invoke(CLEAR_GLOBAL_HOTKEY_CHANNEL_NAME),
  getSessionToken: () => ipcRenderer.invoke("get-session-token"),
  getBackendPort: () => ipcRenderer.invoke(BACKEND_PORT_CHANNEL_NAME),
  // Image storage operations (deprecated, use saveFile/getFileData)
  saveImage: (fileData: ArrayBuffer, filename: string): Promise<string> =>
    ipcRenderer.invoke(SAVE_IMAGE_CHANNEL_NAME, fileData, filename),
  getImageData: (filePath: string): Promise<string> => ipcRenderer.invoke(GET_IMAGE_DATA_CHANNEL_NAME, filePath),
  // File storage operations
  saveFile: (fileData: ArrayBuffer, filename: string): Promise<string> =>
    ipcRenderer.invoke(SAVE_FILE_CHANNEL_NAME, fileData, filename),
  getFileData: (filePath: string): Promise<string> => ipcRenderer.invoke(GET_FILE_DATA_CHANNEL_NAME, filePath),
});
