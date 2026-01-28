/** During setup we sometimes need to read the user config file before the backend is fully initialized.
 * This file provides a minimal fallback implementation of the config module to allow that to happen.
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { autoUpdater } from "electron-updater";
import toml from "toml";

// SYNC WITH PYTHON: sculptor/utils/build.py get_sculptor_folder()
/**
 * Get the sculptor folder path.
 * This matches the Python implementation in sculptor/utils/build.py
 *
 * Returns: ~/.sculptor or ~/.dev_sculptor (or SCULPTOR_FOLDER env var if set)
 */
export const getSculptorFolder = (): string => {
  const homeDir = os.homedir();
  // 1. Check SCULPTOR_FOLDER env var first, that overrides all else
  const sculptorFolderEnv = process.env.SCULPTOR_FOLDER;
  if (sculptorFolderEnv !== undefined) {
    return sculptorFolderEnv;
  } else if (autoUpdater.currentVersion.includes("dev")) {
    return path.join(homeDir, ".dev_sculptor");
  } else {
    return path.join(homeDir, ".sculptor");
  }
};

/**
 * Attempts to read the updateChannel from the config.toml file on disk, if we could not get a clean result from the
 * backend.
 *
 * NOTE: Please keep this in sync with the UpdateChannel field and config file behaviour in imbue_core/imbue_core/sculptor/user_config.py
 *
 * @returns The update channel string if found, or null if unable to read
 */
export function readUpdateChannelFromDisk(): "STABLE" | "ALPHA" {
  const sculptorFolder = getSculptorFolder();
  // We do not care about looking in
  const configLocation = path.join(sculptorFolder, "config.toml");
  try {
    if (fs.existsSync(configLocation)) {
      // toml library parse from file:
      const config = toml.parse(fs.readFileSync(configLocation, "utf-8"));
      // The field is stored as update_channel (snake_case) in TOML
      return config.update_channel || "STABLE";
    }
  } finally {
    // Ignore errors, return null below
  }
  return "STABLE";
}
