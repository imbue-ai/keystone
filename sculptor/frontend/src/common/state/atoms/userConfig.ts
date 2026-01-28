import { atom } from "jotai";
import { atomWithStorage } from "jotai/utils";

import { isLlmModel } from "~/common/Guards.ts";

import type { UserConfig } from "../../../api";
import { LlmModel } from "../../../api";

/**
 * PRIMARY ATOM: Global UserConfig State
 *
 * This atom serves as the single source of truth for all user configuration.
 * It mirrors the backend UserConfig model.
 *
 * Lifecycle:
 * 1. Initialized as null on app startup
 * 2. Populated during app initialization via GET /api/v1/config
 * 3. Updated whenever settings are changed via PUT /api/v1/config
 * 4. Eventually will be updated via WebSocket streams when we migrate to database storage
 */
export const userConfigAtom = atom<UserConfig | null>(null);

/**
 * DERIVED ATOMS: Semantic Setting Accessors
 *
 * These atoms provide clean, typed access to specific settings while automatically
 * reacting to changes in the primary userConfig atom. This pattern eliminates the
 * need for manual subscription management and ensures consistent behavior.
 */

// Theme setting - replaces existing localStorage-based themeAtom
export const appThemeAtom = atom<"light" | "dark" | "system">((get) => {
  const config = get(userConfigAtom);
  return (config?.appTheme as "light" | "dark" | "system") ?? "system";
});

// Keyboard shortcuts - typed and validated

export const doesSendMessageShortcutIncludeModifierAtom = atom<boolean | undefined>(
  (get) => get(userConfigAtom)?.doesSendMessageShortcutIncludeModifier,
);

export const newAgentShortcutAtom = atom<string | undefined>((get) => get(userConfigAtom)?.newAgentShortcut);

export const searchAgentsShortcutAtom = atom<string | undefined>((get) => get(userConfigAtom)?.searchAgentsShortcut);

export const toggleSidebarShortcutAtom = atom<string | undefined>((get) => get(userConfigAtom)?.toggleSidebarShortcut);

export const globalHotkeyAtom = atom<string | undefined>((get) => get(userConfigAtom)?.globalHotkey);

// Model preferences

export const lastUsedModelAtom = atomWithStorage<string | null>("sculptor-last-used-model", null);

export const configuredDefaultModelAtom = atom<string | null>((get) => get(userConfigAtom)?.defaultLlm ?? null);

export const defaultModelAtom = atom<string>((get) => {
  const configuredDefaultModel = get(configuredDefaultModelAtom);
  if (configuredDefaultModel && isLlmModel(configuredDefaultModel)) {
    return configuredDefaultModel;
  }
  const lastUsedModel = get(lastUsedModelAtom);
  if (lastUsedModel && isLlmModel(lastUsedModel)) {
    return lastUsedModel;
  }
  return LlmModel.CLAUDE_4_SONNET;
});

// User identity settings
export const userEmailAtom = atom<string | undefined>((get) => get(userConfigAtom)?.userEmail);

export const gitUsernameAtom = atom<string | undefined>((get) => get(userConfigAtom)?.userGitUsername);

export const hasSeenPairingModeModalAtom = atom<boolean | undefined>(
  (get) => get(userConfigAtom)?.hasSeenPairingModeModal,
);

export const areSuggestionsEnabledAtom = atom<boolean>((get) => get(userConfigAtom)?.areSuggestionsEnabled ?? true);

export const isScoutBetaFeatureOnAtom = atom<boolean>((get) => get(userConfigAtom)?.isScoutBetaFeatureOn ?? false);

export const imbueVerifyRunFrequencyAtom = atom<string>(
  (get) => get(userConfigAtom)?.imbueVerifyRunFrequency ?? "auto",
);

export const imbueVerifyTokenUsageRequirementAtom = atom<string>(
  (get) => get(userConfigAtom)?.imbueVerifyTokenUsageRequirement ?? "low",
);

export const isForkingBetaFeatureOnAtom = atom<boolean>((get) => get(userConfigAtom)?.isForkingBetaFeatureOn ?? false);

export const isPairingModeStashingBetaFeatureOnAtom = atom<boolean>(
  (get) => get(userConfigAtom)?.isPairingModeStashingBetaFeatureOn ?? false,
);

export const isPairingModeWarningBeforeStashEnabledAtom = atom<boolean>(
  (get) => get(userConfigAtom)?.isPairingModeWarningBeforeStashEnabled ?? true,
);

export const isClaudeConfigurationSynchronizedAtom = atom<boolean>(
  (get) => get(userConfigAtom)?.isClaudeConfigurationSynchronized ?? false,
);

export const areDevSuggestionsOnAtom = atom<boolean>((get) => get(userConfigAtom)?.areDevSuggestionsOn ?? false);

export const updateChannelAtom = atom<string>((get) => get(userConfigAtom)?.updateChannel ?? "stable");

// Privacy & telemetry settings
export type TelemetrySettings = {
  isErrorReportingEnabled: boolean;
  isProductAnalyticsEnabled: boolean;
  isLlmLogsEnabled: boolean;
  isSessionRecordingEnabled: boolean;
  isRepoBackupEnabled: boolean;
  isPrivacyPolicyConsented: boolean;
  isTelemetryLevelSet: boolean;
  isFullContribution: boolean;
};

export const telemetrySettingsAtom = atom<TelemetrySettings>((get) => {
  const config = get(userConfigAtom);
  return {
    isErrorReportingEnabled: config?.isErrorReportingEnabled ?? false,
    isProductAnalyticsEnabled: config?.isProductAnalyticsEnabled ?? false,
    isLlmLogsEnabled: config?.isLlmLogsEnabled ?? false,
    isSessionRecordingEnabled: config?.isSessionRecordingEnabled ?? false,
    isRepoBackupEnabled: config?.isRepoBackupEnabled ?? false,
    isPrivacyPolicyConsented: config?.isPrivacyPolicyConsented ?? false,
    isTelemetryLevelSet: config?.isTelemetryLevelSet ?? false,
    isFullContribution: config?.isFullContribution ?? false,
  };
});
