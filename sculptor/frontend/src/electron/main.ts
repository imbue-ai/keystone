import { spawn } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { setTimeout as delay } from "node:timers/promises";

import { randomBytes } from "crypto";
import type { MenuItemConstructorOptions } from "electron";
import { app, BrowserWindow, dialog, globalShortcut, ipcMain, Menu, shell } from "electron";
import Store from "electron-store";

import type { UpdateChannel } from "../api";
import type { AnyBackendStatus } from "../shared/types";
import { autoUpdaterManager } from "./autoUpdater";
import { readUpdateChannelFromDisk } from "./configFallback";
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
} from "./constants";
import { PORT } from "./electronOnlyUtils";
import { logger } from "./logger";

const isInPytest = !!process.env.PYTEST_CURRENT_TEST;
/* eslint-disable @typescript-eslint/naming-convention */
const IS_DEVELOPMENT = process.env.NODE_ENV === "development";
const IS_MAC = process.platform === "darwin";
const IS_LINUX = process.platform === "linux";

// Sculptor mode configuration
const SCULPTOR_MODE = process.env.SCULPTOR_MODE || "default";
const VALID_MODES = ["default", "client_only", "headless"] as const;
type SculptorMode = (typeof VALID_MODES)[number];

// Validate the mode
if (!VALID_MODES.includes(SCULPTOR_MODE as SculptorMode)) {
  logger.warn(`[main] Invalid SCULPTOR_MODE '${SCULPTOR_MODE}', defaulting to 'default'`);
} else {
  logger.info(`[main] Starting Sculptor in '${SCULPTOR_MODE}' mode`);
}
/* eslint-enable @typescript-eslint/naming-convention */

// We need to pass these flags to disable the keychain as early as possible.
if (IS_MAC) {
  // For macOS: Use mock keychain to avoid prompts
  // This prevents Chromium from accessing the real keychain
  app.commandLine.appendSwitch("use-mock-keychain");
}

if (IS_LINUX) {
  // For Linux: Use basic (unencrypted) storage to avoid keyring prompts
  app.commandLine.appendSwitch("password-store", "basic");
}

let pythonBackgroundProcess: ReturnType<typeof spawn> | null = null;
let window: BrowserWindow | null = null;
let currentBackendStatus: AnyBackendStatus = { status: "loading", payload: { message: "Initializing..." } };
let stderrBuffer = "";
let currentGlobalHotkey: string | null = null;
let isQuitting = false;

const MAX_STDERR_BUFFER_SIZE = 10 * 1024 * 1024; // 10 MB
const MAX_BYTE_PER_CHARACTER = 4; // at worst characters are 4 bytes in JS
const MAX_CHARACTERS_IN_BUFFER = Math.ceil(MAX_STDERR_BUFFER_SIZE / MAX_BYTE_PER_CHARACTER);

// Constants for timeouts and intervals
const PRODUCTION_BACKEND_READINESS_TIMEOUT_MS = 20000; // 20 secs
const DEVELOPMENT_BACKEND_READINESS_TIMEOUT_MS = 10000; // 10 secs
const TESTING_BACKEND_READINESS_TIMEOUT_MS = 60000; // 60 secs
const RETRY_INTERVAL_MS = 1000; // 1 second
const INITIAL_WAIT_MS = 2000; // 2 seconds initial wait before first check

// Window configuration constants
const WINDOW_WIDTH = 1200;
const WINDOW_HEIGHT = 800;
const MIN_WINDOW_WIDTH = 600;
const MIN_WINDOW_HEIGHT = 600;

const SCULPTOR_ARG_PREFIX = "--sculptor=";

// Generate a session token for CSRF-like protection
// Allow using an external session token via environment variable (for remote headless sculptor connections)
// Falls back to generating a random token if not provided
const SESSION_TOKEN = process.env.SCULPTOR_SESSION_TOKEN || randomBytes(32).toString("hex");

// Log whether we're using an external or generated token (without revealing the actual token)
if (process.env.SCULPTOR_SESSION_TOKEN) {
  logger.info("[main] Using session token from SCULPTOR_SESSION_TOKEN environment variable");
} else {
  logger.info("[main] Generated new session token for this session");
}

// Important: Keep the following two block executed as early as possible.
const userDataOverride = process.env.SCULPTOR_USER_DATA_DIR;
if (userDataOverride) {
  app.setPath("userData", userDataOverride);
} else if (!app.isPackaged) {
  app.setPath("userData", app.getPath("userData") + "-unpackaged");
}

if (app.isPackaged) {
  // Prevent multiple instances for the packaged version of the app.
  // We don't enforce that for unpackaged apps since it can still be useful for testing.
  // For more documentation, look for 74643a8d-5e1d-4b5d-9b36-62cafce687ca.
  if (!app.requestSingleInstanceLock()) {
    logger.warn("[main] Another instance of Sculptor is already running, quitting.");
    process.exit(1);
  }

  const focusWindow = (): void => {
    if (!isQuitting && window) {
      window.focus();
    }
  };
  app.on("second-instance", focusWindow);
  app.on("activate", focusWindow);
} else {
  autoUpdaterManager.configureDevelopmentMode();
}

const store = new Store({
  defaults: {
    windowBounds: {
      width: WINDOW_WIDTH,
      height: WINDOW_HEIGHT,
    },
  },
});

const sleep = (ms: number): Promise<void> => {
  return new Promise((resolve) => setTimeout(resolve, ms));
};

const sendBackendState = (state: AnyBackendStatus): void => {
  currentBackendStatus = state;
  if (window?.webContents) {
    window.webContents.send(BACKEND_STATUS_CHANGE_CHANNEL_NAME, state);
  }
};

// Wait for backend to be ready by polling the health endpoint
const waitForBackend = async (port: number, host = "127.0.0.1"): Promise<boolean> => {
  const baseUrl = `http://${host}:${port}`;
  const start = performance.now();
  const timeoutMs = isInPytest
    ? TESTING_BACKEND_READINESS_TIMEOUT_MS
    : IS_DEVELOPMENT
      ? DEVELOPMENT_BACKEND_READINESS_TIMEOUT_MS
      : PRODUCTION_BACKEND_READINESS_TIMEOUT_MS;

  logger.info(`[main] waiting for backend at ${baseUrl}/api/v1/health`);

  // Give the backend process some time to start before first check
  await sleep(INITIAL_WAIT_MS);

  while (performance.now() - start < timeoutMs) {
    if (isQuitting) {
      return false;
    }

    try {
      // TODO: verify that all backend services are ready, not just the HTTP server.
      const response = await fetch(`${baseUrl}/api/v1/health`);
      if (response.ok) {
        const healthData = await response.text();
        logger.info(`[main] backend ready, health data: ${healthData}`);
        return true;
      }
    } catch {
      // Backend not ready yet, this is expected during startup
      const elapsed = Math.round((performance.now() - start) / 1000);
      logger.info(`[main] backend not ready yet (${elapsed}s elapsed), retrying...`);
    }

    await sleep(RETRY_INTERVAL_MS);
  }

  logger.warn(`Backend failed to start within ${timeoutMs / 1000} seconds`);
  return false;
};

// Apply user's update channel preference by fetching config from backend
const applyUpdateChannelPreference = async (): Promise<void> => {
  const deadline = Date.now() + 20_000;

  while (Date.now() < deadline) {
    const remaining = deadline - Date.now();
    const attemptTimeout = Math.min(2_000, remaining);
    try {
      const res = await fetch(`http://127.0.0.1:${await PORT}/api/v1/config`, {
        headers: { "X-Session-Token": SESSION_TOKEN },
        signal: AbortSignal.timeout(attemptTimeout),
      });
      if (res.ok) {
        const { updateChannel: updateChannel = "STABLE" } = (await res.json()) as {
          updateChannel?: UpdateChannel;
        };
        logger.info(`[main] Applying update channel preference: ${updateChannel}`);
        autoUpdaterManager.setUpdateChannel(updateChannel);
        return;
      }

      // Bail early on client errors: we should use the default next
      if (res.status >= 400 && res.status < 500) {
        logger.warn(`[main] Failed to fetch user config (status ${res.status}); using default`);
        break;
      }

      logger.debug(`[main] Backend not ready (status ${res.status}); retrying in 2s`);
    } catch (err) {
      logger.debug(`[main] Backend not ready yet; retrying in 2s (${String(err)})`);
    }

    const wait = Math.min(2_000, Math.max(0, deadline - Date.now()));
    if (wait > 0) await delay(wait);
  }

  logger.warn("[main] Failed fetching user: we will attempt to read the file directly");

  autoUpdaterManager.setUpdateChannel(readUpdateChannelFromDisk());
};

// Where to launch the sidecar from in DEV vs PROD
const getBackendCommand = async (): Promise<{ cmd: string; args: Array<string> }> => {
  // Get command line arguments, filtering out Electron-specific ones
  // In packaged apps, arguments might come from different sources
  logger.info("[main] Raw process.argv:", process.argv);
  logger.info("[main] app.isPackaged:", app.isPackaged);

  // Skip the binary path, plus the project directory in dev mode
  // (https://github.com/electron/electron/issues/4690).
  const argv = process.argv.slice(app.isPackaged ? 1 : 2);
  const userArgs = argv.flatMap((arg) =>
    arg.startsWith(SCULPTOR_ARG_PREFIX) ? [arg.slice(SCULPTOR_ARG_PREFIX.length)] : [],
  );

  logger.info("[main] Filtered user args:", userArgs);

  // Base arguments for sculptor_main
  const baseArgs = ["--port", String(await PORT), "--no-open-browser", "--packaged-entrypoint"];

  const exe = `sculptor_backend/sculptor_backend${process.platform === "win32" ? ".exe" : ""}`;
  const bin = path.join(resourcesPath(), exe);
  // Pass through all user arguments to sculptor_main
  return { cmd: bin, args: [...baseArgs, ...userArgs] };
};

const resourcesPath = (): string => {
  // Packaged apps have resource files defined in extraResource in forge.config.ts available.
  // In development, Electron Forge doesn't copy those files,
  // but we can find them relative to SCULPTOR_FRONTEND_DIR (set in electron:start script in package.json).
  return app.isPackaged ? process.resourcesPath : path.join(process.env.SCULPTOR_FRONTEND_DIR!, "../dist");
};

const urlStartsWith = (url1: string, url2: string): boolean => {
  return url1 === url2 || url1.startsWith(url2.endsWith("/") ? url2 : url2 + "/");
};

const toggleDevTools = (window: BrowserWindow): void => {
  if (window.webContents.isDevToolsOpened()) {
    window.webContents.closeDevTools();
  } else {
    window.webContents.openDevTools({ mode: "detach" });
  }
};

// Editable context menu (for input fields, textareas, etc.)
const editableContextMenu = Menu.buildFromTemplate([
  { role: "cut" },
  { role: "copy" },
  { role: "paste" },
  { type: "separator" },
  { role: "selectAll" },
]);

// Non-editable context menu (for everything else)
const uneditableContextMenu = Menu.buildFromTemplate([{ role: "copy" }, { type: "separator" }, { role: "selectAll" }]);

const createApplicationMenu = (): void => {
  const isMac = process.platform === "darwin";

  const template: Array<MenuItemConstructorOptions> = [
    ...(isMac
      ? [
          {
            label: app.name,
            submenu: [
              {
                label: "Check for Updates...",
                click: (): void => {
                  autoUpdaterManager.checkForUpdatesAndNotify();
                },
              },
              { type: "separator" as const },
              { role: "hide" as const },
              { role: "hideOthers" as const },
              { role: "unhide" as const },
              { type: "separator" as const },
              { role: "quit" as const },
            ],
          },
        ]
      : [
          {
            label: "File",
            submenu: [{ role: "quit" as const }],
          },
        ]),
    {
      label: "Edit",
      submenu: [
        { role: "undo" as const },
        { role: "redo" as const },
        { type: "separator" as const },
        { role: "cut" as const },
        { role: "copy" as const },
        { role: "paste" as const },
        { role: "selectAll" as const },
      ],
    },
    {
      label: "View",
      submenu: [
        { role: "reload" as const },
        { role: "forceReload" as const },
        { role: "toggleDevTools" as const },
        { type: "separator" as const },
        { role: "resetZoom" as const },
        { role: "zoomIn" as const },
        { role: "zoomOut" as const },
        { type: "separator" as const },
        { role: "togglefullscreen" as const },
      ],
    },
    {
      label: "Window",
      submenu: [
        { role: "minimize" as const },
        { role: "zoom" as const },
        ...(isMac ? [{ role: "close" as const }] : []),
      ],
    },
    ...(!isMac
      ? [
          {
            label: "Help",
            submenu: [
              {
                label: "Check for Updates...",
                click: (): void => {
                  autoUpdaterManager.checkForUpdatesAndNotify();
                },
              },
            ],
          },
        ]
      : []),
  ];

  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
};

const createWindow = async (): Promise<void> => {
  const savedBounds = store.get("windowBounds", {
    width: WINDOW_WIDTH,
    height: WINDOW_HEIGHT,
  }) as { width: number; height: number; x?: number; y?: number };

  if (isInPytest) {
    savedBounds.width = 1600;
    savedBounds.height = 1000;
  }

  window = new BrowserWindow({
    width: savedBounds.width,
    height: savedBounds.height,
    x: savedBounds.x,
    y: savedBounds.y,
    minWidth: MIN_WINDOW_WIDTH,
    minHeight: MIN_WINDOW_HEIGHT,
    icon: path.join(__dirname, "..", "..", "assets", "icons", "icon.png"),
    titleBarStyle: process.platform === "darwin" ? "hiddenInset" : "default",
    skipTaskbar: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  const zoomFactor = process.env.SCULPTOR_ZOOM_FACTOR;
  if (zoomFactor) {
    logger.info(`[main] registering event handler to set zoom factor to ${zoomFactor}`);
    window.webContents.on("did-finish-load", () => {
      window!.webContents.setZoomFactor(Number(zoomFactor));
      logger.info(`[main] set zoom factor to ${zoomFactor}`);
    });
  }

  const appUrl = IS_DEVELOPMENT
    ? // The standard way to get the dev server URL is using MAIN_WINDOW_VITE_DEV_SERVER_URL,
      // populated by Vite using its define mechanism.
      // However, defines are substituted during compilation and reflected in the compiled JavaScript,
      // so this creates a race condition when launching multiple dev instances at the same time,
      // which is exactly what we do during integration tests:
      // different instances will compete to write their respective frontend URLs to the compiled file,
      // so other instances may load an Electron window with the wrong URL.
      //
      // So instead,
      // we construct the frontend URL using SCULPTOR_FRONTEND_PORT, which is populated at runtime,
      // so different dev instances will open their corresponding frontend URLs correctly.
      `http://localhost:${process.env.SCULPTOR_FRONTEND_PORT || "5173"}`
    : // Note: we load the built frontend from a file in production which requires us to circumvent CORS in the backend.
      `file://${path.join(app.getAppPath(), ".vite/build/renderer/index.html")}`;

  logger.info("[main] Initial URL:", appUrl);
  await window.loadURL(appUrl);

  window.webContents.on("context-menu", (_event, params) => {
    if (!window) return; // Only to prove to the type checker that window is non-null below
    if (params.isEditable) {
      editableContextMenu.popup({ window, x: params.x, y: params.y });
    } else {
      uneditableContextMenu.popup({ window, x: params.x, y: params.y });
    }
  });

  // This is necessary to prevent the ttyd terminal from blocking window close events.
  window.webContents.on("will-prevent-unload", (e) => {
    e.preventDefault();
  });

  // Don't let the window navigate away from the Sculptor app.
  // This handles links with target="_blank".
  window.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url);
    return { action: "deny" };
  });

  // This handles other links.
  window.webContents.on("will-navigate", (event, url) => {
    if (!urlStartsWith(url, appUrl)) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });

  // Set the main window reference for auto-updater IPC communication
  autoUpdaterManager.setMainWindow(window);

  const saveWindowBounds = (): void => {
    if (!window) return;
    const bounds = window.getBounds();
    store.set("windowBounds", bounds);
  };

  window.on("resize", saveWindowBounds);
  window.on("move", saveWindowBounds);

  // Development-only configuration
  if (!app.isPackaged) {
    const focusHotkey = process.env.SCULPTOR_FOCUS_HOTKEY;
    if (focusHotkey) {
      globalShortcut.register(focusHotkey, (): void => {
        window?.show();
        window?.focus();
      });
    }
  }

  // Register local shortcuts for dev tools (only when window is focused)
  window.webContents.on("before-input-event", (event, input) => {
    if (input.key === "F12" && input.type === "keyDown") {
      toggleDevTools(window!);
    }

    if (process.platform === "darwin" && input.key === "i" && input.meta && input.alt && input.type === "keyDown") {
      toggleDevTools(window!);
    }
  });

  window.on("closed", (): void => {
    window = null;
  });
};

const checkForUpdates = async (): Promise<void> => {
  // In production, first check if there's a pending update from a previous download
  const hasPendingUpdate = await autoUpdaterManager.checkForPendingUpdate();
  if (hasPendingUpdate) {
    // If an update was already downloaded, install it immediately instead of continuing startup
    autoUpdaterManager.installPendingUpdate();
    // The app will quit and install, so we return early to avoid continuing startup
    return;
  }

  // No pending update: proceed with normal update checks.

  // We want to check for updates right away and then periodically. The backend may not be running, and may be bricked
  // due to a bad update. We still want the autoupdater to run independently, so that we can ship a rescue-update to
  // our users if that happens.

  // This call fetches the user's update channel preference from the backend. It will wait 20 seconds, but then will
  // set the default preference.
  applyUpdateChannelPreference()
    .then(() => {
      // Once preference is applied (or timeout/fallback occurs), check for updates
      autoUpdaterManager.checkForUpdatesAndNotify();
      autoUpdaterManager.startPeriodicUpdateCheck();
    })
    .catch((err) => {
      logger.error("Error applying update channel preference:", err);
      logger.error("Continuing with default update channel.");
      autoUpdaterManager.checkForUpdatesAndNotify();
      autoUpdaterManager.startPeriodicUpdateCheck();
    });
};

app.whenReady().then(async () => {
  // Add any IPC handlers here, see sculptor/frontend/src/preload.ts where these are made available to the frontend
  autoUpdaterManager.initializeIpcHandlers();

  // Create application menu
  createApplicationMenu();

  ipcMain.handle(BACKEND_PORT_CHANNEL_NAME, () => PORT);
  ipcMain.handle(SELECT_PROJECT_DIRECTORY_CHANNEL_NAME, async () => {
    if (!window) {
      return null;
    }
    const result = await dialog.showOpenDialog(window, {
      properties: ["openDirectory"],
      title: "Select Project Directory",
      buttonLabel: "Select",
    });
    return result.canceled ? null : result.filePaths[0];
  });

  ipcMain.handle(GET_CURRENT_BACKEND_STATUS_CHANNEL_NAME, () => {
    return currentBackendStatus;
  });

  // Global hotkey IPC handlers
  ipcMain.handle(SET_GLOBAL_HOTKEY_CHANNEL_NAME, async (event, hotkey: string) => {
    try {
      // Unregister previous hotkey
      if (currentGlobalHotkey) {
        globalShortcut.unregister(currentGlobalHotkey);
      }

      // Register new hotkey
      const isSuccess = globalShortcut.register(hotkey, () => {
        if (!isQuitting && window) {
          if (window.isVisible() && window.isFocused()) {
            window.hide();
          } else {
            window.show();
            window.focus();
          }
        }
      });

      if (isSuccess) {
        currentGlobalHotkey = hotkey;
        // Enable always-on-top behavior when global hotkey is set
        if (window) {
          window.setAlwaysOnTop(true);
          window.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
        }
        logger.info(`Global hotkey registered: ${hotkey}`);
        return { success: true };
      } else {
        logger.error(`Failed to register global hotkey: ${hotkey}`);
        return { success: false, error: `Failed to register hotkey: ${hotkey}` };
      }
    } catch (error) {
      logger.error("Error setting global hotkey:", error);
      return { success: false, error: error.message };
    }
  });

  ipcMain.handle(CLEAR_GLOBAL_HOTKEY_CHANNEL_NAME, async () => {
    try {
      if (currentGlobalHotkey) {
        globalShortcut.unregister(currentGlobalHotkey);
        currentGlobalHotkey = null;
        // Disable always-on-top behavior when global hotkey is cleared
        if (window) {
          window.setAlwaysOnTop(false);
          window.setVisibleOnAllWorkspaces(false);
        }
        logger.info("Global hotkey cleared");
      }
      return { success: true };
    } catch (error) {
      logger.error("Error clearing global hotkey:", error);
      return { success: false, error: error.message };
    }
  });

  ipcMain.handle("get-session-token", () => {
    return SESSION_TOKEN;
  });

  ipcMain.handle(SAVE_IMAGE_CHANNEL_NAME, async (_event, fileData: ArrayBuffer, originalFilename: string) => {
    try {
      const userDataPath = app.getPath("userData");
      const imagesDir = path.join(userDataPath, "images");

      if (!fs.existsSync(imagesDir)) {
        fs.mkdirSync(imagesDir, { recursive: true });
      }

      const { randomUUID } = await import("crypto");
      const uuid = randomUUID();
      const ext = path.extname(originalFilename);
      const uniqueFilename = `${uuid}${ext}`;
      const filePath = path.join(imagesDir, uniqueFilename);

      fs.writeFileSync(filePath, Buffer.from(fileData));

      logger.info(`Image saved to: ${filePath}`);
      return filePath;
    } catch (error) {
      logger.error("Error saving image:", error);
      throw error;
    }
  });

  ipcMain.handle(GET_IMAGE_DATA_CHANNEL_NAME, async (_event, filePath: string) => {
    try {
      const fileBuffer = fs.readFileSync(filePath);
      const base64Data = fileBuffer.toString("base64");

      const ext = path.extname(filePath).toLowerCase();
      const mimeTypes: Record<string, string> = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
      };
      if (!mimeTypes[ext]) {
        throw new Error(`Unsupported image extension: ${ext}`);
      }
      const mimeType = mimeTypes[ext];

      return `data:${mimeType};base64,${base64Data}`;
    } catch (error) {
      logger.error("Error getting image data:", error);
      throw error;
    }
  });

  // New file storage handlers
  ipcMain.handle(SAVE_FILE_CHANNEL_NAME, async (_event, fileData: ArrayBuffer, originalFilename: string) => {
    try {
      const userDataPath = app.getPath("userData");
      const filesDir = path.join(userDataPath, "files");

      if (!fs.existsSync(filesDir)) {
        fs.mkdirSync(filesDir, { recursive: true });
      }

      const { randomUUID } = await import("crypto");
      const uuid = randomUUID();
      const ext = path.extname(originalFilename);
      const uniqueFilename = `${uuid}${ext}`;
      const filePath = path.join(filesDir, uniqueFilename);

      fs.writeFileSync(filePath, Buffer.from(fileData));

      logger.info(`File saved to: ${filePath}`);
      return filePath;
    } catch (error) {
      logger.error("Error saving file:", error);
      throw error;
    }
  });

  ipcMain.handle(GET_FILE_DATA_CHANNEL_NAME, async (_event, filePath: string) => {
    try {
      const fileBuffer = fs.readFileSync(filePath);
      const base64Data = fileBuffer.toString("base64");

      const ext = path.extname(filePath).toLowerCase();
      const mimeTypes: Record<string, string> = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        // ".pdf": "application/pdf",
      };
      if (!mimeTypes[ext]) {
        throw new Error(`Unsupported file extension: ${ext}`);
      }
      const mimeType = mimeTypes[ext];

      return `data:${mimeType};base64,${base64Data}`;
    } catch (error) {
      logger.error("Error getting file data:", error);
      throw error;
    }
  });

  // We can only create the window _after_ the handlers have been defined, because createWindow() invokes preload.ts
  // which depends on the handlers.
  await createWindow();
  if (app.isPackaged) {
    await checkForUpdates();
  }

  const startTime = performance.now();

  // Determine if backend should be started based on mode
  let shouldStartBackend = false;

  if (SCULPTOR_MODE === "client_only") {
    // Client-only mode: Never start backend
    shouldStartBackend = false;
    logger.info("[main] Running in client_only mode - backend will not be started");
  } else if (SCULPTOR_MODE === "headless") {
    // Headless mode: Start backend but minimize/hide UI
    shouldStartBackend = true;
    logger.info("[main] Running in headless mode - backend will start, UI will be minimized");
    // TODO: Implement headless mode properly
    // Options to consider:
    // 1. Run without creating a window at all (make our packaged appImage linux bundle only depend optionally on xwindows).
    // 2. Create a hidden window that never shows
    // 3. Create a minimal blank window
    // 4. Use app.dock.hide() on macOS to hide from dock
    // For now, just minimize the window after creation
    if (window) {
      window.minimize();
    }
  } else {
    // Default mode: Original behavior
    shouldStartBackend = !IS_DEVELOPMENT || process.env.START_BACKEND_IN_DEV;
    logger.info(
      `[main] Running in default mode (client + backend) - backend startup: ${shouldStartBackend} because IS_DEV ${IS_DEVELOPMENT} env.START_BACKEND_IN_DEV ${process.env.START_BACKEND_IN_DEV}`,
    );
  }

  if (shouldStartBackend) {
    const { cmd, args } = await getBackendCommand();
    logger.info("[main] spawning backend without initial project:", cmd, args.join(" "));

    sendBackendState({ status: "loading", payload: { message: "Waiting for backend..." } });
    pythonBackgroundProcess = spawn(cmd, args, {
      stdio: ["pipe", "pipe", "pipe"],
      env: {
        ...process.env,
        PATH: `${resourcesPath()}/mutagen:${process.env.PATH}`,
        SESSION_TOKEN: SESSION_TOKEN,
      },
      // Do not propagate sigint to child processes when Ctrl+C is pressed in the terminal.
      detached: true,
    });

    pythonBackgroundProcess.stdout?.on("data", (data) => {
      try {
        process.stdout.write(data);
      } catch {
        // This can fail if the app has crashed,
        // and not catching the error results in annoying popups.
        // So swallow the error.
      }
    });

    pythonBackgroundProcess.stderr?.on("data", (data) => {
      stderrBuffer += data.toString();

      const excessLength = stderrBuffer.length - MAX_CHARACTERS_IN_BUFFER;
      // logger.debug(excessLength);
      if (excessLength > 0) {
        // remove excess characters from the start of the buffer
        stderrBuffer = stderrBuffer.slice(excessLength, stderrBuffer.length);
      }

      try {
        process.stderr.write(data);
      } catch {
        // This can fail if the app has crashed,
        // and not catching the error results in annoying popups.
        // So swallow the error.
      }
    });

    // This only triggers if the command fails to start
    pythonBackgroundProcess.on("error", (err: Error) => {
      logger.error("[main] backend spawn error:", err);
      if (!isQuitting) {
        sendBackendState({
          status: "error",
          payload: {
            message: err.message,
            stack: err.stack,
          },
        });
      }
    });

    // Triggers when the command dies for any reason
    pythonBackgroundProcess.on("exit", (code, signal) => {
      if (isQuitting) {
        return;
      }

      logger.warn(`[main] backend exited code=${code} signal=${signal}`);
      const exitMessage = signal ? `Backend killed with signal ${signal}` : `Backend exited with code ${code}`;
      sendBackendState({
        status: "exited",
        payload: {
          code,
          signal,
          stderr: stderrBuffer.trim(),
          message: exitMessage,
        },
      });
    });

    logger.info("[main] backend process started, waiting for it to be ready...");
  } else {
    logger.info("[main] skipping starting the python backend (it should already be running)");
  }

  // In client_only mode, still try to connect to backend (which should be running remotely)
  const isRunning = await waitForBackend(await PORT);

  if (isRunning && !isQuitting) {
    const totalTime = performance.now() - startTime;
    if (shouldStartBackend) {
      logger.info(`[main] backend fully ready (total startup time: ${totalTime}ms)`);
    }

    sendBackendState({
      status: "running",
      payload: {
        message: "Backend is running.",
      },
    });
  } else {
    if (
      !isInPytest &&
      currentBackendStatus.status !== "error" &&
      currentBackendStatus.status !== "exited" &&
      !isQuitting
    ) {
      throw new Error("Tried to start the backend but it failed and we did not properly set our backend status.");
    }
  }
});

app.on("before-quit", async (e): Promise<void> => {
  if (!isQuitting) {
    e.preventDefault();
    isQuitting = true;
    sendBackendState({ status: "shutting_down", payload: { message: "Shutting down..." } });
    globalShortcut.unregisterAll();
    autoUpdaterManager.stopPeriodicUpdateCheck();
    try {
      if (pythonBackgroundProcess) {
        await killProcessAndWait(pythonBackgroundProcess, 32000);
      }
    } catch (error) {
      logger.error("Error killing backend process:", error);
    }

    if (IS_DEVELOPMENT) app.dock?.hide();
    app.quit();
  }
});

function killProcessAndWait(process: ReturnType<typeof spawn>, timeoutMs: number): Promise<void> {
  return new Promise((resolve, reject) => {
    if (!process || process.killed) {
      resolve();
      return;
    }

    const timeout = setTimeout(() => {
      reject(new Error("Process kill timeout"));
    }, timeoutMs);

    process.once("exit", () => {
      clearTimeout(timeout);
      resolve();
    });

    process.once("error", (err) => {
      clearTimeout(timeout);
      reject(err);
    });

    process.kill("SIGTERM");
  });
}

app.on("window-all-closed", (): void => {
  // On macOS, it's common for applications to stay open when all windows are closed.
  if (!isQuitting && process.platform !== "darwin") {
    app.quit();
  }
});

// This gets triggered when user clicks the app icon in the Dock in macOS.
app.on("activate", async () => {
  if (window !== null) {
    window.show();
    window.focus();
    return;
  }

  if (isQuitting) {
    dialog.showMessageBox({
      type: "info",
      title: "Shutting Down",
      message: "The application is quitting and cannot be reopened at this time.",
      detail: "Please wait a moment before reopening the application.",
    });
    return;
  }
  await createWindow();
});
