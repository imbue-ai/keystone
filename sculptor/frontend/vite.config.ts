// TODO (PROD-2161): `vite.renderer.config.ts` should inherit from this config so that both our web and electron builds are close to equivalent
import { sentryVitePlugin } from "@sentry/vite-plugin";
import react from "@vitejs/plugin-react-swc";
import { execSync } from "child_process";
import { defineConfig, loadEnv, type UserConfig } from "vite";
import path from "node:path";

// This is just a backup function to use in case SCULPTOR_SENTRY_RELEASE_ID is not set.
const getGitSha = (): string => {
  try {
    return execSync("git rev-parse --short HEAD", { encoding: "utf8" }).trim();
  } catch {
    return "unknown";
  }
};

const ENABLED_PLUGINS = [
  react({
    plugins: [
      [
        "@swc/plugin-styled-components",
        {
          displayName: true,
          fileName: true,
          ssr: false,
        },
      ],
    ],
  }),
  {
    name: "generate-types",
    buildStart(): void {
      console.log("Generating dynamic types...");
      execSync("npm run generate-api", { stdio: "inherit" });
    },
  },
];

// If Sentry is enabled, add the Sentry plugin to the list of plugins.
// It MUST be last.
if (process.env.SENTRY_AUTH_TOKEN) {
  ENABLED_PLUGINS.push(
    sentryVitePlugin({
      authToken: process.env.SENTRY_AUTH_TOKEN,
      org: "generally-intelligent-e3",
      project: "sculptor-frontend",
      // TODO: in theory this would be cool but its broken (on the sentry side)
      // reactComponentAnnotation: {
      //   enabled: true,
      // }
    }),
  );
}

// For more info: https://github.com/vitejs/vite-plugin-react-swc
/* eslint-disable-next-line import/no-default-export */
export default defineConfig(({ command, mode }): UserConfig => {
  const env = loadEnv(mode, process.cwd(), "");

  const sentryDsn = env.SCULPTOR_FRONTEND_SENTRY_DSN || "";
  const sentryRelease = env.SCULPTOR_SENTRY_RELEASE_ID || `${getGitSha()}`;

  const apiBaseUrl: string = env.SCULPTOR_API_BASE_URL || "";

  const baseConfig: UserConfig = {
    define: {
      FRONTEND_SENTRY_DSN: JSON.stringify(sentryDsn),
      FRONTEND_SENTRY_RELEASE_ID: JSON.stringify(sentryRelease),
      API_URL_BASE: JSON.stringify(apiBaseUrl),
    },
    build: {
      sourcemap: true,
    },
    clearScreen: false,
    server: {
      port: 5174,
      strictPort: true,
      host: "127.0.0.1",
    },
    plugins: ENABLED_PLUGINS,
    envPrefix: "SCULPTOR_",
    resolve: {
      alias: {
        "~": path.resolve(__dirname, "src"),
      },
    },
  };

  console.log(`Started vite with command: "${command}" and mode: "${mode}"`);
  console.log(`Sentry DSN: ${sentryDsn}`);
  console.log(`Sentry Release: ${sentryRelease}`);

  if (command === "serve" || mode === "development") {
    const apiPort = Number(env.SCULPTOR_API_PORT || 5050);
    const fePort = Number(env.SCULPTOR_FRONTEND_PORT || 5174);

    console.log(`Proxying frontend: SCULPTOR_API_PORT=${apiPort} and SCULPTOR_FRONTEND_PORT=${fePort}`);

    return {
      ...baseConfig,
      // this configures the proxy server when running in development mode
      server: {
        port: fePort,
        strictPort: true,
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
      },
    };
  }
  return baseConfig;
});
