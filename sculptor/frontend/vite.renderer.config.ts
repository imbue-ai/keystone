import path from "node:path";
import { fileURLToPath } from "node:url";

import { sentryVitePlugin } from "@sentry/vite-plugin";
import react from "@vitejs/plugin-react-swc";
import { defineConfig, loadEnv, type UserConfig } from "vite";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const root = __dirname;
const inputHtml = path.resolve(__dirname, "index.html");

/* eslint-disable-next-line import/no-default-export */
export default defineConfig(({ command, mode }): UserConfig => {
  const env = loadEnv(mode, process.cwd(), "");

  const sentryDsn = env.SCULPTOR_FRONTEND_SENTRY_DSN || "";
  const sentryRelease = env.SCULPTOR_SENTRY_RELEASE_ID || "";

  console.log(`Started vite renderer with command: "${command}" and mode: "${mode}"`);
  console.log(`Sentry DSN: ${sentryDsn}`);
  console.log(`Sentry Release: ${sentryRelease}`);

  const ENABLED_PLUGINS = [react()];

  // If Sentry is enabled, add the Sentry plugin to the list of plugins.
  // It MUST be last.
  if (process.env.SENTRY_AUTH_TOKEN) {
    ENABLED_PLUGINS.push(
      sentryVitePlugin({
        authToken: process.env.SENTRY_AUTH_TOKEN,
        org: "generally-intelligent-e3",
        project: "sculptor-frontend",
      }),
    );
  }

  const baseConfig: UserConfig = {
    root,
    define: {
      FRONTEND_SENTRY_DSN: JSON.stringify(sentryDsn),
      FRONTEND_SENTRY_RELEASE_ID: JSON.stringify(sentryRelease),
      // When serving with Electron,
      // preload.ts injects the backend port into the window.sculptor.backendPort,
      // and we leave this undefined so that the frontend will use that instead.
      API_URL_BASE: "undefined",
    },
    build: {
      sourcemap: true,
      // By default, forge will bundle everything in `.vite/build`.
      outDir: ".vite/build/renderer",
      emptyOutDir: true,
      rollupOptions: {
        input: { main: inputHtml },
      },
    },
    clearScreen: false,
    // Makes asset paths relative so it works even if you load via file:// later
    base: "./",
    plugins: ENABLED_PLUGINS,
    envPrefix: "SCULPTOR_",
    resolve: {
      alias: {
        "~": path.resolve(__dirname, "src"),
      },
    },
  };

  if (command === "serve" || mode === "development") {
    const apiPort = Number(env.SCULPTOR_API_PORT || 5050);
    const fePort = Number(env.SCULPTOR_FRONTEND_PORT || 5173);

    console.log(`Proxying renderer: SCULPTOR_API_PORT=${apiPort} and SCULPTOR_FRONTEND_PORT=${fePort}`);

    return {
      ...baseConfig,
      // this configures the proxy server when running in development mode
      server: {
        port: fePort,
        strictPort: true,
        host: "127.0.0.1",
        proxy: {
          "/api": {
            target: `http://127.0.0.1:${apiPort}`,
            changeOrigin: true,
            ws: true,
          },
          "/ws": {
            target: `http://127.0.0.1:${apiPort}`,
            ws: true,
            rewriteWsOrigin: true,
          },
        },
        // HMR can cause race conditions in integration tests, so disable it.
        hmr: !env.PYTEST_CURRENT_TEST,
      },
    };
  }
  return baseConfig;
});
