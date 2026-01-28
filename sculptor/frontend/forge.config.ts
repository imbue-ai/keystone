// Import Node.js path utility for resolving file paths across platforms
import os from "node:os";
import path from "node:path";

// Import Electron Fuses types and options for security configuration
import { FuseV1Options, FuseVersion } from "@electron/fuses";
import type { MakerDMGConfig } from "@electron-forge/maker-dmg";
// Import the Fuses plugin to apply security settings at build time
import { FusesPlugin } from "@electron-forge/plugin-fuses";
// Import Vite plugin for modern bundling and development experience
import { VitePlugin } from "@electron-forge/plugin-vite";
import type { ForgeConfig, ForgeMakeResult } from "@electron-forge/shared-types";

const appleApiKey = path.resolve(__dirname, "config/AuthKey_VJJ5VUQ73R.p8");
const appleApiKeyId = "VJJ5VUQ73R";
const appleApiIssuer = "d4149adb-1d4a-4077-a426-8ef45ef3b9ef";

// eslint-disable-next-line @typescript-eslint/naming-convention
const IS_NOTARIZING_AND_SIGNING = !process.env.SKIP_NOTARIZE_AND_SIGN;

// Helper to run a tool and show stderr on failure
async function run(cmd: string, args: Array<string>): Promise<boolean> {
  const { promisify } = await import("node:util");
  const { execFile } = await import("node:child_process");
  const execFileP = promisify(execFile);

  try {
    const { stdout, stderr } = await execFileP(cmd, args, { env: process.env });
    if (stdout?.trim()) console.log(stdout.trim());
    if (stderr?.trim()) console.warn(stderr.trim());
    return true;
  } catch (err) {
    type PrintableError = { stderr?: string; message?: string };
    console.warn(
      `${cmd} ${args.join(" ")} failed: ${(err as PrintableError)?.stderr || (err as PrintableError)?.message}`,
    );
    return false;
  }
}

const platform = os.platform();
const arch = os.arch();

let config = {
  // Configuration for the Electron packager that creates the final app bundle
  packagerConfig: {
    // Enable ASAR packaging - bundles app files into a single archive for performance
    asar: true,
    // The out parameter will be ignored by electron forge, so we're setting it to the default for clarity.
    out: "out/",
    // Include additional resources in the packaged app.
    extraResource: [
      // TODO: We should namespace this based on arch
      path.resolve(__dirname, "../dist/sculptor_backend"), // Our backend
      path.resolve(__dirname, "../dist/mutagen"), // For syncing
      // FIXME(danver): Namespace this based on arch too.
      path.resolve(__dirname, `updater_config/${platform}/${arch}/app-update.yml`), // Required by electron-updater
    ],
    // Path to application icon (platform-specific extensions will be auto-selected)
    icon: path.resolve(__dirname, "assets/icons/icon"),
    name: "Sculptor",
    productName: "Sculptor",
    executableName: "Sculptor",
  },
  // Configuration for rebuilding native modules (empty - using defaults)
  rebuildConfig: {},
  // Array of "makers" that create platform-specific installers and packages
  makers: [
    {
      // Windows installer maker using Squirrel.Windows
      name: "@electron-forge/maker-squirrel",
      config: {
        // Name of the Windows setup executable file
        setupExe: "sculptor-setup.exe",
      },
      // Only run this maker on Windows platforms
      platforms: ["win32"],
    },
    {
      // ZIP archive maker for simple distribution
      // The Zip archive is also used by the auto-updater on Macs.
      name: "@electron-forge/maker-zip",
      // Create ZIP files for all platforms (fallback distribution method)
      platforms: ["darwin", "linux", "win32"],
    },
    {
      // macOS DMG disk image maker for native macOS distribution
      name: "@electron-forge/maker-dmg",
      // NOTE: when making changes here, you'll need to "eject" the previous DMG for changes to appear
      config: (arch: unknown): MakerDMGConfig => ({
        background: "./assets/dmg_background.png",
        format: "UDZO",
        icon: "./assets/dmg_icon.png", // Volume icon
        overwrite: true,
        contents: [
          {
            x: 444,
            y: 249,
            type: "link",
            path: "/Applications",
          },
          {
            x: 222,
            y: 249,
            type: "file",
            path: `./out/Sculptor-darwin-${arch}/Sculptor.app`,
          },
          // Hide "hidden icons" even when users are showing everything (does create a scrollbar)
          {
            x: 200,
            y: 600,
            type: "position",
            path: ".background",
          },
          {
            x: 100,
            y: 600,
            type: "position",
            path: ".VolumeIcon.icns",
          },
        ],
        additionalDMGOptions: {
          "background-color": "#FFFFFF",
          window: {
            size: {
              width: 666,
              height: 498,
            },
          },
        },
        name: "Sculptor",
      }),
      // Only run on macOS (darwin)
      platforms: ["darwin"],
    },
    {
      // AppImage Maker for Linux Distributions
      name: "@reforged/maker-appimage",
      config: {
        options: {
          name: "sculptor",
          productName: "Sculptor",
          categories: ["development"],
          bin: "Sculptor",
        },
      },
      //Only run on Linux platforms
      platforms: ["linux"],
    },
  ],
  // Array of plugins that extend Electron Forge functionality
  plugins: [
    {
      // Plugin that automatically unpacks native Node modules from ASAR
      // This is needed for native modules that can't run from within ASAR archives
      name: "@electron-forge/plugin-auto-unpack-natives",
      config: {},
    },
    // Vite plugin configuration for modern JavaScript bundling and hot reload
    new VitePlugin({
      // Build configuration for main process and preload scripts
      build: [
        // Main Electron process entry point and its Vite config
        { entry: "src/electron/main.ts", config: "vite.main.config.ts" },
        // Preload script (runs in renderer but has Node access) and its config
        { entry: "src/preload.ts", config: "vite.preload.config.ts" },
      ],
      // Renderer process configuration (the web UI part of the app)
      renderer: [{ name: "main_window", config: "vite.renderer.config.ts" }],
    }),

    // Fuses plugin for security hardening - disables potentially dangerous features
    // These settings are applied at build time and cannot be changed at runtime
    new FusesPlugin({
      // Use version 1 of the fuses system
      version: FuseVersion.V1,
      // Disable running as Node.js (prevents access to Node APIs in renderer)
      [FuseV1Options.RunAsNode]: false,
      // Enable cookie encryption for better security in web contexts
      [FuseV1Options.EnableCookieEncryption]: true,
      // Disable NODE_OPTIONS environment variable to prevent runtime modifications
      [FuseV1Options.EnableNodeOptionsEnvironmentVariable]: false,
      // Disable Node.js inspector/debugger CLI arguments for production security
      [FuseV1Options.EnableNodeCliInspectArguments]: false,
      // Enable ASAR integrity validation to detect tampering
      [FuseV1Options.EnableEmbeddedAsarIntegrityValidation]: true,
      // Only load app code from ASAR archive (prevents loading external code)
      [FuseV1Options.OnlyLoadAppFromAsar]: true,
    }),
  ],
  hooks: {
    // Runs after all makers finish (i.e., after the DMG is created)
    postMake: async (_forgeConfig: ForgeConfig, results: Array<ForgeMakeResult>): Promise<void> => {
      if (!IS_NOTARIZING_AND_SIGNING) {
        console.log("You skipped signing, so let it happen");
        return;
      }

      // There is a known bug/shortcoming in Electron Forge where it fails to notarize the DMG, so we need to do that
      // ourselves.
      const darwinContainers = results
        .filter(({ platform }) => platform === "darwin")
        .flatMap(({ artifacts }) => artifacts.filter((file) => file.endsWith(".dmg")));

      for (const file of darwinContainers) {
        console.log(`\n🔐 Processing macOS container: ${file}`);

        // 1) Try to staple directly (works if a ticket exists for this exact file)
        const isStapled = await run("xcrun", ["stapler", "staple", file]);

        // 2) If no stapled ticket, submit the container itself, then staple
        if (!isStapled) {
          console.log("Submitting container to Apple notarization (notarytool --wait)...");
          const isSubmitted = await run("xcrun", [
            "notarytool",
            "submit",
            file,
            "--key",
            String(appleApiKey),
            "--key-id",
            String(appleApiKeyId),
            "--issuer",
            String(appleApiIssuer),
            "--wait",
          ]);
          if (!isSubmitted) continue;

          // Try stapling again after approval
          await run("xcrun", ["stapler", "staple", file]);
        }
        console.log(`✅ Finished: ${file}\n`);
      }
    },
  },
  publishers: [
    // We evaluated @electron-forge/publisher-s3, but now just use s3-sync to achieve the same thing.
  ],
  publish: [
    {
      provider: "generic",
      url: "https://imbue-sculptor-releases.s3.us-west-2.amazonaws.com/sculptor/zip/darwin/arm64",
    },
  ],
};

if (IS_NOTARIZING_AND_SIGNING) {
  config = {
    ...config,
    packagerConfig: {
      ...config.packagerConfig,

      // @ts-expect-error: ignore spurious error
      // macOS Code Signing (for organization account - production):
      osxSign: {
        // FOR ORGANIZATION ACCOUNT: Use "Developer ID Application" certificate
        // IMPORTANT: Use EXACT identity string from: security find-identity -v -p codesigning
        // Example output: "Developer ID Application: Company Name (ABC123XYZ)"
        identity: "Developer ID Application: Imbue, Inc. (LDDYAR29MP)",
        // OR use environment variable for CI: process.env.CSC_NAME
        // Enable hardened runtime (required for notarization)
        "hardened-runtime": true,
        // Entitlements file for sandbox permissions
        entitlements: "config/entitlements.mac.plist",
        "entitlements-inherit": "config/entitlements.mac.plist",
        // Additional signing options
        "signature-flags": "library",
      },

      // macOS Notarization (ONLY available with paid Organization Developer Program):
      osxNotarize: {
        appleApiKey: appleApiKey,
        appleApiKeyId: appleApiKeyId,
        appleApiIssuer: appleApiIssuer,
      },

      // This bundleId needs to stay constant forever; will break autoupdate if we ever change this.
      appBundleId: "com.electron.sculptor",
    },
  };
}

// eslint-disable-next-line import/no-default-export
export default config;
