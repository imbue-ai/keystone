import os from "node:os";
import path from "node:path";

import { app } from "electron";
import log from "electron-log";

// TODO: we probably want to come back and revisit how we get the directory
const LOG_PATH = path.join(
  os.homedir(),
  app.isPackaged ? ".sculptor" : ".dev_sculptor",
  "logs",
  "electron",
  "electron.log",
);

const configureLogger = (): void => {
  // Don't overwrite the entire transport, just modify properties
  log.transports.file.level = "info";
  log.transports.file.maxSize = 100 * 1024 * 1024; // 100MB
  // log.transports.file.sync = true;
  log.transports.file.resolvePathFn = (): string => LOG_PATH;

  // Note: electron log has default log cycling behavior
  // log.transports.file.archiveLogFn = ...

  log.transports.console.level = "debug";
};

configureLogger();

log.info(`Configured logger to log at ${log.transports.file.getFile().path}`);

export { log as logger };
