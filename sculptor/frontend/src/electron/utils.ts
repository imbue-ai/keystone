// TODO: This file should probably live somewhere else so that it's clear it can't import electron things.
// It's unintuitive but you cannot import electron things here (such as the commented line below) because this file is imported both in electron flavor JS and in browser flavor JS.
// import { autoUpdater } from "electron-updater";

/**
 * Check if the app is running in Electron environment
 * This checks for the presence of the sculptor API exposed via contextBridge
 */
export const isElectron = (): boolean => {
  return typeof window !== "undefined" && window.sculptor !== undefined;
};

/**
 * Open a native directory selection dialog
 * @returns The selected directory path or null if cancelled
 * @throws Error if not in Electron environment
 */
export const selectProjectDirectory = async (): Promise<string | null> => {
  if (isElectron() && window.sculptor?.selectProjectDirectory) {
    return await window.sculptor.selectProjectDirectory();
  }
  throw Error("selectProjectDirectory is only available in Electron environment");
};

// Titlebar constants
export const TITLEBAR_HEIGHT = 40;
export const SIDEBAR_CLOSED_LEFT_PADDING = 80;
export const SIDEBAR_OPEN_LEFT_PADDING = 20;
export const getTitleBarLeftPadding = (isSidebarOpen: boolean): string => {
  // On macOS, the titlebar traffic light buttons are on the left, so we need to add padding
  if (!isMac()) {
    return "12px";
  }
  return isSidebarOpen ? `${SIDEBAR_OPEN_LEFT_PADDING}px` : `${SIDEBAR_CLOSED_LEFT_PADDING}px`;
};

export const isMac = (): boolean => {
  return window.sculptor?.platform === "darwin";
};

export const getMetaKey = (): string => {
  return isMac() ? "⌘" : "Ctrl";
};

export const isModifierPressed = (e: KeyboardEvent | React.KeyboardEvent): boolean => {
  return isMac() ? e.metaKey : e.ctrlKey;
};
