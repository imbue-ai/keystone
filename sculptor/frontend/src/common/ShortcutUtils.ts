/**
 * Utility functions for handling keyboard shortcuts
 */
import { useCallback } from "react";

import { isMac, isModifierPressed } from "../electron/utils.ts";

export type ShortcutParsed = {
  meta: boolean;
  ctrl: boolean;
  alt: boolean;
  shift: boolean;
  key: string;
};

/**
 * Parse a shortcut string like "Ctrl+N" or "Meta+P" into component parts
 */
export const parseShortcut = (shortcutString: string): ShortcutParsed => {
  const parts = shortcutString.toLowerCase().split("+");
  const result: ShortcutParsed = {
    meta: false,
    ctrl: false,
    alt: false,
    shift: false,
    key: "",
  };

  for (const part of parts) {
    const trimmed = part.trim();
    switch (trimmed) {
      case "meta":
      case "cmd":
      case "⌘":
        result.meta = true;
        break;
      case "ctrl":
      case "control":
      case "⌃":
        result.ctrl = true;
        break;
      case "alt":
      case "option":
      case "⌥":
        result.alt = true;
        break;
      case "shift":
      case "⇧":
        result.shift = true;
        break;
      default:
        // This should be the actual key
        result.key = trimmed;
        break;
    }
  }

  return result;
};

/**
 * Check if a KeyboardEvent matches a parsed shortcut
 */
export const matchesShortcut = (event: KeyboardEvent, shortcut: ShortcutParsed): boolean => {
  return (
    event.metaKey === shortcut.meta &&
    event.ctrlKey === shortcut.ctrl &&
    event.altKey === shortcut.alt &&
    event.shiftKey === shortcut.shift &&
    event.key.toLowerCase() === shortcut.key.toLowerCase()
  );
};

/**
 * Check if a KeyboardEvent matches a shortcut string
 */
export const matchesShortcutString = (event: KeyboardEvent, shortcutString: string): boolean => {
  const parsed = parseShortcut(shortcutString);
  return matchesShortcut(event, parsed);
};

/**
 * Convert shortcut modifiers to platform-specific symbols
 */
export const formatShortcutForDisplay = (shortcut: string | undefined): string => {
  if (!shortcut) {
    return "";
  }

  return shortcut
    .split("+")
    .map((part) => {
      const trimmed = part.trim().toLowerCase();
      switch (trimmed) {
        case "cmd":
          return "⌘";
        case "meta":
          return isMac() ? "⌘" : "⌃";
        case "ctrl":
        case "control":
          return "⌃";
        case "alt":
        case "option":
          return "⌥";
        case "shift":
          return "⇧";
        default:
          return part.trim().toUpperCase();
      }
    })
    .join("");
};

type UseModifiedEnterOptions = {
  onConfirm: () => void;
  doesSendMessageShortcutIncludeModifier: boolean | undefined;
};

export const useModifiedEnter = ({
  onConfirm,
  doesSendMessageShortcutIncludeModifier,
}: UseModifiedEnterOptions): ((e: KeyboardEvent) => boolean) => {
  return useCallback(
    (e: KeyboardEvent): boolean => {
      if (e.key !== "Enter") {
        return false;
      }

      let shouldSend = false;

      if (doesSendMessageShortcutIncludeModifier) {
        // Modifier+Enter sends the message, Enter alone inserts newline
        shouldSend = isModifierPressed(e);
      } else {
        // Enter alone sends the message, Shift+Enter inserts newline
        shouldSend = !e.shiftKey;
      }

      if (shouldSend) {
        onConfirm();
        // Tell TipTap we handled this event
        return true;
      }

      return false;
    },
    [onConfirm, doesSendMessageShortcutIncludeModifier],
  );
};
