import type { BrowserWindow } from "electron";
import { ipcMain } from "electron";
import type { ProgressInfo, UpdateInfo } from "electron-updater";
import { autoUpdater } from "electron-updater";

import type { DownloadDockerTarRequest, UpdateChannel } from "../api";
import type { AutoUpdaterErrorInfo } from "./autoUpdaterTypes";
import { AutoUpdaterChannel } from "./autoUpdaterTypes";
import { PORT } from "./electronOnlyUtils";
import { logger } from "./logger";

const UPDATE_CHECK_INTERVAL_MS = 30 * 60 * 1000;

const BASE_UPDATE_URL = "https://imbue-sculptor-releases.s3.us-west-2.amazonaws.com";

/**
 * Makes a promise that will wait for the downloads of the control plane and devcontainers, if they are included in the update info
 */
function getExtraDownloadsPromise(info: UpdateInfo): Promise<Array<void>> {
  const ghcrImages = info.files
    .map((info) => {
      return info.url;
    })
    .filter((url) => {
      return url.startsWith("ghcr.io/imbue-ai/");
    });

  const promises = ghcrImages.map(async (url: string) => {
    const request_data: DownloadDockerTarRequest = { url: url };

    const baseUrl = `http://localhost:${await PORT}`;
    await fetch(`${baseUrl}/api/v1/download_docker_tar_to_cache`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request_data),
    });
    return;
  });

  return Promise.all(promises);
}

/**
 * Auto-updater module for handling application updates
 * Provides event handling and IPC communication with the renderer process
 */
export class AutoUpdaterManager {
  private mainWindow: BrowserWindow | null = null;
  private updateCheckInterval: NodeJS.Timeout | null = null;

  // Are we currently in the process of quitting for an Update?
  private quittingForUpdate: boolean = false;
  private updateReady: boolean = false;
  private extraDownloadPromise: Promise<Array<void>> | null = null;

  constructor() {
    this.setupEventHandlers();
  }

  /**
   * Configure auto-updater for development mode
   * Forces electron-updater to look for dev-app-update.yml
   */
  public configureDevelopmentMode(): void {
    // Tell electron-updater to operate in dev
    autoUpdater.forceDevUpdateConfig = true; // look for dev-app-update.yml
  }

  /**
   * Set the main window reference for IPC communication
   */
  public setMainWindow(window: BrowserWindow): void {
    this.mainWindow = window;
  }

  /**
   * Initialize IPC handlers for auto-updater commands from renderer
   */
  public initializeIpcHandlers(): void {
    // Handler for checking updates
    ipcMain.handle("auto-updater-check-for-updates", async () => {
      try {
        const result = await autoUpdater.checkForUpdates();
        return result ? { updateInfo: result.updateInfo } : null;
      } catch (error) {
        logger.error("Error checking for updates:", error);
        return { error: (error as Error).message };
      }
    });

    // Handler for downloading updates
    ipcMain.handle("auto-updater-download-update", async () => {
      try {
        await autoUpdater.downloadUpdate();
        return { success: true };
      } catch (error) {
        logger.error("Error downloading update:", error);
        return { error: (error as Error).message };
      }
    });

    // Handler for installing updates
    ipcMain.handle("auto-updater-quit-and-install", () => {
      this.quittingForUpdate = true;
      autoUpdater.quitAndInstall(false, true);
    });

    // Handler for getting current version
    ipcMain.handle("auto-updater-get-current-version", () => {
      return autoUpdater.currentVersion.version;
    });

    // Handler for setting auto-download
    ipcMain.handle("auto-updater-set-auto-download", (_, enabled: boolean) => {
      autoUpdater.autoDownload = enabled;
      return { success: true };
    });
  }

  /**
   * Configure the update feed URL based on user preference
   * Should be called before checkForUpdates to take effect
   */
  public setUpdateChannel(channel: UpdateChannel): void {
    const platform = process.platform;
    const arch = process.arch;

    // Map process.arch to the expected arch format
    const archMap: Record<string, string> = {
      arm64: "arm64",
      x64: "x64",
      x86_64: "x64", // Normalize x86_64 to x64
    };

    const normalizedArch = archMap[arch] || arch;
    // Determine the path segment based on channel

    const channelPathMap: Record<string, string> = {
      STABLE: "sculptor",
      ALPHA: "sculptor-alpha",
    };
    const channelPath = channelPathMap[channel] || "sculptor";

    // Determine the format path based on platform
    let formatPath: string;
    if (platform === "darwin") {
      formatPath = "zip/darwin";
    } else if (platform === "linux") {
      formatPath = "AppImage";
    } else {
      logger.error(`[autoUpdater] Unsupported platform for update channel: ${platform}`);
      return;
    }

    const url = `${BASE_UPDATE_URL}/${channelPath}/${formatPath}/${normalizedArch}`;

    logger.info(`[autoUpdater] Setting update feed URL to: ${url} (channel: ${channel})`);
    try {
      autoUpdater.setFeedURL({
        provider: "generic",
        url: url,
      });
      autoUpdater.allowDowngrade = false;
    } catch (error) {
      logger.error("[autoUpdater] Failed to set feed URL:", error);
    }
  }

  public checkForUpdatesAndNotify(): void {
    autoUpdater.checkForUpdatesAndNotify();
  }

  public startPeriodicUpdateCheck(): void {
    if (this.updateCheckInterval) {
      clearInterval(this.updateCheckInterval);
    }

    this.updateCheckInterval = setInterval(() => {
      logger.info("[autoUpdater] Performing periodic update check...");
      this.checkForUpdatesAndNotify();
    }, UPDATE_CHECK_INTERVAL_MS);

    logger.info("[autoUpdater] Periodic update checking started (every 30 minutes)");
  }

  public stopPeriodicUpdateCheck(): void {
    if (this.updateCheckInterval) {
      clearInterval(this.updateCheckInterval);
      this.updateCheckInterval = null;
      logger.info("[autoUpdater] Periodic update checking stopped");
    }
  }

  /**
   * Setup event handlers for auto-updater events
   */
  private setupEventHandlers(): void {
    autoUpdater.on("checking-for-update", () => {
      logger.info("Checking for update...");
      this.notifyRenderer(AutoUpdaterChannel.CheckingForUpdate);
    });

    autoUpdater.on("update-available", (info: UpdateInfo) => {
      logger.info("Update available.");
      this.extraDownloadPromise = getExtraDownloadsPromise(info);
      this.notifyRenderer(AutoUpdaterChannel.UpdateAvailable, info);
    });

    autoUpdater.on("update-not-available", (info: UpdateInfo) => {
      logger.info("Update not available.");
      this.notifyRenderer(AutoUpdaterChannel.UpdateNotAvailable, info);
    });

    autoUpdater.on("error", (err) => {
      logger.error("Error in auto-updater. " + err);
      const errorInfo: AutoUpdaterErrorInfo = {
        message: err.message,
        stack: err.stack,
      };
      this.notifyRenderer(AutoUpdaterChannel.Error, errorInfo);
    });

    autoUpdater.on("download-progress", (progressObj: ProgressInfo) => {
      const log_message = `Download speed: ${progressObj.bytesPerSecond} - Downloaded ${progressObj.percent}% (${progressObj.transferred}/${progressObj.total})`;
      logger.info(log_message);
      this.notifyRenderer(AutoUpdaterChannel.DownloadProgress, progressObj);
    });

    autoUpdater.on("update-downloaded", (info: UpdateInfo) => {
      // Await the download of control plane and devcontainer (if the promise exists) before notifying renderer and setting updateReady
      const promise = this.extraDownloadPromise ?? Promise.resolve();

      promise.finally(() => {
        logger.info("Update downloaded");
        this.updateReady = true;
        this.notifyRenderer(AutoUpdaterChannel.UpdateDownloaded, info);
      });
    });
  }

  /**
   * Check if an update has been downloaded and is ready to install
   * Should be called during app startup to detect pending updates
   *
   * This method checks for updates and waits briefly to see if electron-updater
   * detects a previously downloaded update file on disk. If found, it will emit
   * the update-downloaded event which sets updateReady to true.
   */
  public async checkForPendingUpdate(): Promise<boolean> {
    try {
      logger.info("[autoUpdater] Checking for pending updates on startup");

      // Check for updates - if a previously downloaded update exists on disk,
      // electron-updater will detect it and emit update-downloaded event
      const updateCheckResult = await autoUpdater.checkForUpdates();
      if (!updateCheckResult) {
        return false;
      }

      // Wait briefly for the update-downloaded event to fire if cached update exists
      // electron-updater validates cached updates synchronously during checkForUpdates
      await new Promise<void>((resolve) => setTimeout(resolve, 100));

      if (this.updateReady) {
        logger.info("[autoUpdater] Detected previously downloaded update ready to install");
        return true;
      }

      return false;
    } catch (error) {
      logger.error("[autoUpdater] Error checking for pending update:", error);
      return false;
    }
  }

  /**
   * Install a pending update immediately
   * Should be called during startup if checkForPendingUpdate returns true
   */
  public installPendingUpdate(): void {
    if (this.updateReady && !this.quittingForUpdate) {
      logger.info("[autoUpdater] Installing pending update on startup");
      this.quittingForUpdate = true;
      autoUpdater.quitAndInstall(false, true); // Quit, install update, restart immediately
    }
  }

  /**
   * Send IPC message to renderer process
   */
  private notifyRenderer(channel: AutoUpdaterChannel, data?: UpdateInfo | ProgressInfo | AutoUpdaterErrorInfo): void {
    if (this.mainWindow && !this.mainWindow.isDestroyed()) {
      this.mainWindow.webContents.send(channel, data);
    }
  }
}

// Export a singleton instance
export const autoUpdaterManager = new AutoUpdaterManager();
