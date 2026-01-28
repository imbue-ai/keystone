import type { ProgressInfo, UpdateInfo } from "electron-updater";
import { useEffect, useState } from "react";

import type { AutoUpdaterErrorInfo, AutoUpdaterEventInfo } from "../electron/autoUpdaterTypes";
import { AutoUpdaterChannel } from "../electron/autoUpdaterTypes";

export type AutoUpdaterState = {
  status: "idle" | "checking" | "available" | "not-available" | "downloading" | "downloaded" | "error";
  updateInfo?: UpdateInfo;
  downloadProgress?: ProgressInfo;
  error?: AutoUpdaterErrorInfo;
  currentVersion?: string;
};

type UseAutoUpdaterReturn = AutoUpdaterState & {
  checkForUpdates: () => Promise<void>;
  downloadUpdate: () => Promise<void>;
  quitAndInstall: () => Promise<void>;
  setAutoDownload: (enabled: boolean) => Promise<void>;
};

/**
 * React hook for managing auto-updater state and events
 */
export const useAutoUpdater = (): UseAutoUpdaterReturn => {
  const [state, setState] = useState<AutoUpdaterState>({ status: "idle" });

  useEffect(() => {
    if (!window.sculptor) return;

    // Set up event listeners
    const handleCheckingForUpdate = (): void => {
      setState((prev) => ({ ...prev, status: "checking" }));
    };

    const handleUpdateAvailable = (_event: unknown, data: AutoUpdaterEventInfo): void => {
      setState((prev) => ({ ...prev, status: "available", updateInfo: data as UpdateInfo }));
    };

    const handleUpdateNotAvailable = (_event: unknown, data: AutoUpdaterEventInfo): void => {
      setState((prev) => ({ ...prev, status: "not-available", updateInfo: data as UpdateInfo }));
    };

    const handleError = (_event: unknown, error: AutoUpdaterEventInfo): void => {
      setState((prev) => ({ ...prev, status: "error", error: error as AutoUpdaterErrorInfo }));
    };

    const handleDownloadProgress = (_event: unknown, progress: AutoUpdaterEventInfo): void => {
      setState((prev) => ({ ...prev, status: "downloading", downloadProgress: progress as ProgressInfo }));
    };

    const handleUpdateDownloaded = (_event: unknown, info: AutoUpdaterEventInfo): void => {
      setState((prev) => ({ ...prev, status: "downloaded", updateInfo: info as UpdateInfo }));
    };

    // Register all event listeners
    window.sculptor.onAutoUpdaterEvent(AutoUpdaterChannel.CheckingForUpdate, handleCheckingForUpdate);
    window.sculptor.onAutoUpdaterEvent(AutoUpdaterChannel.UpdateAvailable, handleUpdateAvailable);
    window.sculptor.onAutoUpdaterEvent(AutoUpdaterChannel.UpdateNotAvailable, handleUpdateNotAvailable);
    window.sculptor.onAutoUpdaterEvent(AutoUpdaterChannel.Error, handleError);
    window.sculptor.onAutoUpdaterEvent(AutoUpdaterChannel.DownloadProgress, handleDownloadProgress);
    window.sculptor.onAutoUpdaterEvent(AutoUpdaterChannel.UpdateDownloaded, handleUpdateDownloaded);

    // Get current version on mount
    void window.sculptor.autoUpdater.getCurrentVersion().then((version) => {
      setState((prev) => ({ ...prev, currentVersion: version }));
    });

    // Cleanup function
    return (): void => {
      if (!window.sculptor) return;

      window.sculptor.removeAutoUpdaterListener(AutoUpdaterChannel.CheckingForUpdate, handleCheckingForUpdate);
      window.sculptor.removeAutoUpdaterListener(AutoUpdaterChannel.UpdateAvailable, handleUpdateAvailable);
      window.sculptor.removeAutoUpdaterListener(AutoUpdaterChannel.UpdateNotAvailable, handleUpdateNotAvailable);
      window.sculptor.removeAutoUpdaterListener(AutoUpdaterChannel.Error, handleError);
      window.sculptor.removeAutoUpdaterListener(AutoUpdaterChannel.DownloadProgress, handleDownloadProgress);
      window.sculptor.removeAutoUpdaterListener(AutoUpdaterChannel.UpdateDownloaded, handleUpdateDownloaded);
    };
  }, []);

  // Action functions
  const checkForUpdates = async (): Promise<void> => {
    if (!window.sculptor) return;

    setState((prev) => ({ ...prev, status: "checking" }));
    const result = await window.sculptor.autoUpdater.checkForUpdates();

    if (result?.error) {
      setState(
        (prev): AutoUpdaterState => ({
          ...prev,
          status: "error",
          error: { message: result.error! },
        }),
      );
    }
  };

  const downloadUpdate = async (): Promise<void> => {
    if (!window.sculptor) return;

    const result = await window.sculptor.autoUpdater.downloadUpdate();

    if (result?.error) {
      setState(
        (prev): AutoUpdaterState => ({
          ...prev,
          status: "error",
          error: { message: result.error! },
        }),
      );
    }
  };

  const quitAndInstall = async (): Promise<void> => {
    if (!window.sculptor) return;

    // This will quit the app and install the update
    await window.sculptor.autoUpdater.quitAndInstall();
  };

  const setAutoDownload = async (enabled: boolean): Promise<void> => {
    if (!window.sculptor) return;

    await window.sculptor.autoUpdater.setAutoDownload(enabled);
  };

  return {
    ...state,
    checkForUpdates,
    downloadUpdate,
    quitAndInstall,
    setAutoDownload,
  };
};
