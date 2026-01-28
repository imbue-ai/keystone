/** This module contains types for the AutoUpdater which are part of our external interface, and which do not depend on
 *   "electron".
 *
 * This makes this module safe to import from the renderer.
 * Note that you cannot import things from electron here (such as below), only type.
 * import { autoUpdater } from "electron-updater";
 */

import type { ProgressInfo, UpdateInfo } from "electron-updater";

// Auto-updater IPC channels as const
export const AutoUpdaterChannel = {
  CheckingForUpdate: "auto-updater-checking-for-update",
  UpdateAvailable: "auto-updater-update-available",
  UpdateNotAvailable: "auto-updater-update-not-available",
  Error: "auto-updater-error",
  DownloadProgress: "auto-updater-download-progress",
  UpdateDownloaded: "auto-updater-update-downloaded",
} as const;

// Type for auto-updater channel values
export type AutoUpdaterChannel = (typeof AutoUpdaterChannel)[keyof typeof AutoUpdaterChannel];

// We still need to define our own error info type as electron-updater doesn't export one
export type AutoUpdaterErrorInfo = {
  message: string;
  stack?: string;
};

export type AutoUpdaterEvents = {
  "checking-for-update": () => void;
  "update-available": (info: UpdateInfo) => void;
  "update-not-available": (info: UpdateInfo) => void;
  error: (error: AutoUpdaterErrorInfo) => void;
  "download-progress": (progress: ProgressInfo) => void;
  "update-downloaded": (info: UpdateInfo) => void;
};

export type AutoUpdaterEventInfo = UpdateInfo | ProgressInfo | AutoUpdaterErrorInfo | undefined;

export type AutoUpdaterEventCallback = (event: unknown, data: AutoUpdaterEventInfo) => void;
