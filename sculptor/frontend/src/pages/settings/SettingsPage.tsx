import {
  AlertDialog,
  Box,
  Button,
  Checkbox,
  Flex,
  Heading,
  Link,
  ScrollArea,
  Select,
  Switch,
  Text,
} from "@radix-ui/themes";
import { useAtomValue } from "jotai";
import { type ReactElement, useState } from "react";

import { HTTPException } from "~/common/Errors.ts";
import { globalDevModeAtom } from "~/common/state/atoms/devMode.ts";
import { useModelCredentials } from "~/common/state/hooks/useModelCredentials.ts";
import { ModelSelectOptions } from "~/components/ModelSelectOptions.tsx";
import { OpenAIAuthButton } from "~/components/OpenAIAuthButton.tsx";
import { VersionDisplay } from "~/components/VersionDisplay.tsx";
import { getMetaKey } from "~/electron/utils.ts";

import { ElementIds, savePrivacySettings, UserConfigField } from "../../api";
import { isSidebarOpenAtom } from "../../common/state/atoms/sidebar.ts";
import type { TelemetrySettings } from "../../common/state/atoms/userConfig.ts";
import {
  appThemeAtom,
  areDevSuggestionsOnAtom,
  areSuggestionsEnabledAtom,
  configuredDefaultModelAtom,
  doesSendMessageShortcutIncludeModifierAtom,
  gitUsernameAtom,
  globalHotkeyAtom,
  imbueVerifyRunFrequencyAtom,
  imbueVerifyTokenUsageRequirementAtom,
  isClaudeConfigurationSynchronizedAtom,
  isForkingBetaFeatureOnAtom,
  isPairingModeStashingBetaFeatureOnAtom,
  isPairingModeWarningBeforeStashEnabledAtom,
  isScoutBetaFeatureOnAtom,
  newAgentShortcutAtom,
  searchAgentsShortcutAtom,
  telemetrySettingsAtom,
  toggleSidebarShortcutAtom,
  updateChannelAtom,
  userEmailAtom,
} from "../../common/state/atoms/userConfig.ts";
import { useUserConfig } from "../../common/state/hooks/useUserConfig.ts";
import { mergeClasses, optional } from "../../common/Utils.ts";
import { AnthropicAuthButton } from "../../components/AnthropicAuthButton.tsx";
import type { TelemetryLevel } from "../../components/TelemetrySettingsSelector.tsx";
import { TelemetrySettingsSelector } from "../../components/TelemetrySettingsSelector.tsx";
import { TitleBar } from "../../components/TitleBar";
import type { ToastContent } from "../../components/Toast.tsx";
import { Toast, ToastType } from "../../components/Toast.tsx";
import { AccountFieldRow } from "./components/AccountFieldRow.tsx";
import { HotkeyField } from "./components/HotkeyField.tsx";
import { ProjectsSection } from "./components/ProjectsSection.tsx";
import { SettingRow } from "./components/SettingRow.tsx";
import styles from "./SettingsPage.module.scss";

export const SettingsSection = {
  GENERAL: "GENERAL",
  ACCOUNT: "ACCOUNT",
  KEYBINDINGS: "KEYBINDINGS",
  REPOSITORIES: "REPOSITORIES",
  EXPERIMENTAL: "EXPERIMENTAL",
} as const;

type SettingsSection = (typeof SettingsSection)[keyof typeof SettingsSection];

export const SettingsPage = (): ReactElement => {
  const [activeSection, setActiveSection] = useState<SettingsSection>(SettingsSection.GENERAL);
  const [editingField, setEditingField] = useState<string | null>(null);
  const [shouldShowAlphaChannelWarning, setShouldShowAlphaChannelWarning] = useState(false);
  const [pendingChannelValue, setPendingChannelValue] = useState<string | null>(null);
  const isSidebarOpen = useAtomValue(isSidebarOpenAtom);

  // Read from derived atoms for current values
  const theme = useAtomValue(appThemeAtom);
  const configuredDefaultModel = useAtomValue(configuredDefaultModelAtom);
  const userEmail = useAtomValue(userEmailAtom);
  const gitUsername = useAtomValue(gitUsernameAtom);
  const newAgentShortcut = useAtomValue(newAgentShortcutAtom);
  const searchAgentsShortcut = useAtomValue(searchAgentsShortcutAtom);
  const toggleSidebarShortcut = useAtomValue(toggleSidebarShortcutAtom);
  const globalHotkey = useAtomValue(globalHotkeyAtom);
  const areSuggestionsEnabled = useAtomValue(areSuggestionsEnabledAtom);
  const isPairingModeStashingBetaFeatureOn = useAtomValue(isPairingModeStashingBetaFeatureOnAtom);
  const isPairingModeWarningBeforeStashEnabled = useAtomValue(isPairingModeWarningBeforeStashEnabledAtom);
  const doesSendMessageShortcutIncludeModifier = useAtomValue(doesSendMessageShortcutIncludeModifierAtom);
  const imbueVerifyRunFrequency = useAtomValue(imbueVerifyRunFrequencyAtom);
  const imbueVerifyTokenUsageRequirement = useAtomValue(imbueVerifyTokenUsageRequirementAtom);
  const isForkingBetaFeatureOn = useAtomValue(isForkingBetaFeatureOnAtom);
  const isClaudeConfigurationSynchronized = useAtomValue(isClaudeConfigurationSynchronizedAtom);
  const areDevSuggestionsOn = useAtomValue(areDevSuggestionsOnAtom);
  const isScoutBetaFeatureOn = useAtomValue(isScoutBetaFeatureOnAtom);
  const updateChannel = useAtomValue(updateChannelAtom);
  const telemetrySettings = useAtomValue(telemetrySettingsAtom);
  const [toast, setToast] = useState<ToastContent | null>(null);
  const isDevMode = useAtomValue(globalDevModeAtom);
  const { hasAnthropicCreds, hasOpenAICreds } = useModelCredentials();

  const { updateField, loadConfig } = useUserConfig();

  // Helper to convert telemetry settings to numeric select value (matching OnboardingWizard)
  const getTelemetryLevelFromSettings = (settings: TelemetrySettings): TelemetryLevel => {
    if (settings.isFullContribution) return "4";
    if (settings.isLlmLogsEnabled) return "3";
    if (settings.isProductAnalyticsEnabled) return "2";
    return "2";
  };

  // Handler for telemetry level changes (matching OnboardingWizard exactly)
  const handleTelemetryLevelChange = async (level: TelemetryLevel): Promise<void> => {
    try {
      await savePrivacySettings({
        body: {
          telemetryLevel: parseInt(level),
          isRepoBackupEnabled: false,
        },
        meta: { skipWsAck: true },
      });
      await loadConfig();
      setToast({
        type: ToastType.SUCCESS,
        title: "Settings updated. You must restart Sculptor for changes to take effect.",
      });
    } catch (error) {
      let errorMessage = "Failed to update telemetry settings";
      if (error instanceof HTTPException) {
        errorMessage = error.detail;
      } else if (error instanceof Error) {
        errorMessage = error.message;
      }
      console.error("Failed to update telemetry settings:", errorMessage);
      setToast({
        type: ToastType.ERROR,
        title: errorMessage,
      });
    }
  };

  // Handler for update channel change with confirmation modal
  const handleUpdateChannelChange = (value: string): void => {
    if (value === "ALPHA" && updateChannel !== "ALPHA") {
      // Show warning modal when switching to Alpha
      setPendingChannelValue(value);
      setShouldShowAlphaChannelWarning(true);
    } else {
      // Allow switching to Stable without warning
      handleSettingChange(UserConfigField.UPDATE_CHANNEL, value);
    }
  };

  const confirmAlphaChannelSwitch = (): void => {
    if (pendingChannelValue) {
      handleSettingChange(UserConfigField.UPDATE_CHANNEL, pendingChannelValue);
    }
    setShouldShowAlphaChannelWarning(false);
    setPendingChannelValue(null);
  };

  const cancelAlphaChannelSwitch = (): void => {
    setShouldShowAlphaChannelWarning(false);
    setPendingChannelValue(null);
  };

  // Handler for updating individual settings - now uses snake_case field names!
  const handleSettingChange = async (fieldConstant: UserConfigField, value: unknown): Promise<void> => {
    try {
      await updateField(fieldConstant, value);

      // Handle global hotkey updates explicitly
      if (fieldConstant === UserConfigField.GLOBAL_HOTKEY) {
        if (window.sculptor) {
          if (value && typeof value === "string") {
            const result = await window.sculptor.setGlobalHotkey(value);
            console.log(result.success ? `Global hotkey set: ${value}` : `Failed to set hotkey: ${result.error}`);
          } else {
            const result = await window.sculptor.clearGlobalHotkey();
            console.log(result.success ? "Global hotkey cleared" : "Failed to clear hotkey");
          }
        }
      }
      setToast({
        type: ToastType.SUCCESS,
        title: "Setting updated",
      });
    } catch (error) {
      console.error(`Failed to update ${fieldConstant}:`, error);
      setToast({
        type: ToastType.ERROR,
        title: `Failed to update setting`,
      });
    }
  };

  const sections: Array<SettingsSection> = [
    SettingsSection.GENERAL,
    SettingsSection.REPOSITORIES,
    SettingsSection.KEYBINDINGS,
    SettingsSection.ACCOUNT,
    SettingsSection.EXPERIMENTAL,
  ];

  return (
    <>
      <Flex height="100%" direction="column" className={styles.container}>
        <Flex position="relative" height="100%" data-testid={ElementIds.SETTINGS_PAGE} flexShrink="1" overflow="hidden">
          <TitleBar shouldShowToggleSidebarButton={!isSidebarOpen} />
          <Box px="6" py="8" width="300px">
            <Heading size="5" className={styles.title}>
              Settings
            </Heading>
            <Flex direction="column" gap="2">
              {sections.map((section) => {
                let testId = "";
                const displayName = section.charAt(0).toUpperCase() + section.slice(1).toLowerCase();

                if (section === SettingsSection.GENERAL) {
                  testId = ElementIds.SETTINGS_NAV_GENERAL;
                } else if (section === SettingsSection.KEYBINDINGS) {
                  testId = ElementIds.SETTINGS_NAV_KEYBINDINGS;
                } else if (section === SettingsSection.ACCOUNT) {
                  testId = ElementIds.SETTINGS_NAV_ACCOUNT;
                } else if (section === SettingsSection.EXPERIMENTAL) {
                  testId = ElementIds.SETTINGS_NAV_EXPERIMENTAL;
                }

                return (
                  <Box
                    key={section}
                    className={mergeClasses(styles.navItem, optional(activeSection === section, styles.active))}
                    onClick={() => setActiveSection(section)}
                    px="3"
                    py="2"
                    data-testid={testId}
                  >
                    {displayName}
                  </Box>
                );
              })}
            </Flex>
          </Box>
          <ScrollArea data-testid={ElementIds.SETTINGS_CONTENT}>
            <Flex py="9">
              {activeSection === SettingsSection.GENERAL && (
                <Flex direction="column" width="100%" px="7">
                  <SettingRow title="Default Model" description="Select the default model for new Agents.">
                    <Select.Root
                      value={configuredDefaultModel ?? "None"}
                      onValueChange={(value) => {
                        if (value === "None") {
                          handleSettingChange(UserConfigField.DEFAULT_LLM, null);
                        } else {
                          handleSettingChange(UserConfigField.DEFAULT_LLM, value);
                        }
                      }}
                    >
                      <Select.Trigger
                        variant="soft"
                        className={styles.settingControl}
                        data-testid={ElementIds.SETTINGS_DEFAULT_MODEL_SELECT}
                      />
                      <Select.Content>
                        <Select.Item key="None" value="None">
                          Most Recently Used
                        </Select.Item>
                        <ModelSelectOptions
                          currentModel={null} // No current model restriction for new tasks
                          hasAnthropicCreds={hasAnthropicCreds}
                          hasOpenAICreds={hasOpenAICreds}
                          shouldDisableOptions={true}
                          optionTestId={ElementIds.SETTINGS_DEFAULT_MODEL_OPTION}
                        />
                      </Select.Content>
                    </Select.Root>
                  </SettingRow>

                  <SettingRow title="Theme" description="Control the appearance of Sculptor">
                    <Select.Root
                      value={theme}
                      onValueChange={(value) => handleSettingChange(UserConfigField.APP_THEME, value)}
                    >
                      <Select.Trigger
                        variant="soft"
                        className={styles.settingControl}
                        data-testid={ElementIds.SETTINGS_THEME_SELECT}
                      />
                      <Select.Content>
                        <Select.Item value="light">Light</Select.Item>
                        <Select.Item value="dark">Dark</Select.Item>
                        <Select.Item value="system">System</Select.Item>
                      </Select.Content>
                    </Select.Root>
                  </SettingRow>
                </Flex>
              )}
              {activeSection === SettingsSection.KEYBINDINGS && (
                <Flex direction="column" width="100%" px="7">
                  <SettingRow title="Send Message" description="Set the keyboard shortcut to send messages">
                    <Select.Root
                      value={doesSendMessageShortcutIncludeModifier ? `${getMetaKey()} + Enter` : "Enter"}
                      onValueChange={(value) => {
                        if (value === "Enter") {
                          handleSettingChange(UserConfigField.DOES_SEND_MESSAGE_SHORTCUT_INCLUDE_MODIFIER, false);
                        } else {
                          handleSettingChange(UserConfigField.DOES_SEND_MESSAGE_SHORTCUT_INCLUDE_MODIFIER, true);
                        }
                      }}
                    >
                      <Select.Trigger variant="soft" />
                      <Select.Content>
                        <Select.Item value="Enter">Enter</Select.Item>
                        <Select.Item value={`${getMetaKey()} + Enter`}>{getMetaKey()} + Enter</Select.Item>
                      </Select.Content>
                    </Select.Root>
                  </SettingRow>
                  <HotkeyField
                    title="Create New Agent"
                    description="Set the keyboard shortcut to create a new Agent within the app"
                    value={newAgentShortcut}
                    onSet={(hotkey) => handleSettingChange(UserConfigField.NEW_AGENT_SHORTCUT, hotkey)}
                    onClear={() => handleSettingChange(UserConfigField.NEW_AGENT_SHORTCUT, "")}
                    elementId={ElementIds.SETTINGS_NEW_AGENT_HOTKEY_FIELD}
                  />
                  <HotkeyField
                    title="Search Agents"
                    description="Set the keyboard shortcut to search your Agents within the app"
                    value={searchAgentsShortcut}
                    onSet={(hotkey) => handleSettingChange(UserConfigField.SEARCH_AGENTS_SHORTCUT, hotkey)}
                    onClear={() => handleSettingChange(UserConfigField.SEARCH_AGENTS_SHORTCUT, "")}
                    elementId={ElementIds.SETTINGS_SEARCH_AGENTS_HOTKEY_FIELD}
                  />
                  <HotkeyField
                    title="Toggle sidebar"
                    description="Set the keyboard shortcut to toggle the sidebar within the app"
                    value={toggleSidebarShortcut}
                    onSet={(hotkey) => handleSettingChange(UserConfigField.TOGGLE_SIDEBAR_SHORTCUT, hotkey)}
                    onClear={() => handleSettingChange(UserConfigField.TOGGLE_SIDEBAR_SHORTCUT, "")}
                    elementId={ElementIds.SETTINGS_TOGGLE_SIDEBAR_HOTKEY_FIELD}
                  />
                </Flex>
              )}
              {activeSection === SettingsSection.ACCOUNT && (
                <Flex direction="column" width="100%" px="7">
                  <AccountFieldRow
                    title="Email Address"
                    description="Email address associated with your account"
                    value={userEmail ?? ""}
                    readOnly={true}
                    isEditing={false}
                    onEdit={() => {}}
                    onSave={() => {}}
                    onCancel={() => {}}
                    elementId={ElementIds.SETTINGS_EMAIL_FIELD}
                  />

                  <AccountFieldRow
                    title="Git Username"
                    description="Your git username, used to author commit messages"
                    value={gitUsername ?? ""}
                    isEditing={editingField === UserConfigField.USER_GIT_USERNAME}
                    onEdit={() => setEditingField(UserConfigField.USER_GIT_USERNAME)}
                    onSave={(value) => {
                      handleSettingChange(UserConfigField.USER_GIT_USERNAME, value);
                      setEditingField(null);
                    }}
                    onCancel={() => setEditingField(null)}
                    elementId={ElementIds.SETTINGS_GIT_USERNAME_FIELD}
                    inputTestId={ElementIds.SETTINGS_GIT_USERNAME_INPUT}
                    saveButtonTestId={ElementIds.SETTINGS_GIT_USERNAME_SAVE_BUTTON}
                    editButtonTestId={ElementIds.SETTINGS_GIT_USERNAME_EDIT_BUTTON}
                  />

                  <SettingRow
                    title="Usage Data & Privacy"
                    description="Control what data Sculptor collects to improve it for you and others. Change this in Settings."
                  >
                    <TelemetrySettingsSelector
                      value={getTelemetryLevelFromSettings(telemetrySettings)}
                      onValueChange={handleTelemetryLevelChange}
                    />
                  </SettingRow>

                  <Flex direction="column" width="100%" py="4" className={styles.settingRow}>
                    <SettingRow
                      description="Authenticate with your Anthropic account or change your API key"
                      title="Anthropic Access"
                    >
                      <AnthropicAuthButton
                        buttonVariant="soft"
                        data-testid={ElementIds.SETTINGS_ANTHROPIC_AUTH_BUTTON}
                      />
                    </SettingRow>
                    <SettingRow
                      description="Authenticate with your OpenAI account or change your API key"
                      title="OpenAI Access (Beta)"
                    >
                      <OpenAIAuthButton buttonVariant="soft" />
                    </SettingRow>
                  </Flex>
                </Flex>
              )}
              {activeSection === SettingsSection.REPOSITORIES && <ProjectsSection setToast={setToast} />}
              {activeSection === SettingsSection.EXPERIMENTAL && (
                <Flex direction="column" width="100%" px="7">
                  <SettingRow
                    title="Forking"
                    description="Forking is a beta feature that will allow you to fork the conversational and file state of an agent into a new agent"
                  >
                    <Switch
                      checked={isForkingBetaFeatureOn}
                      onCheckedChange={(checked) =>
                        handleSettingChange(UserConfigField.IS_FORKING_BETA_FEATURE_ON, checked)
                      }
                      variant="soft"
                    />
                  </SettingRow>
                  <SettingRow
                    title="Pairing Mode Stashing"
                    description="Pairing Mode Stashing is a beta feature that has sculptor stash your current work under the refs/sculptor namespace while Pairing Mode is running. You will be able to pop the sculptor stash even if there is an issue with Pairing Mode."
                    footer={
                      isPairingModeStashingBetaFeatureOn ? (
                        <Flex align="center" gap="2" mt="4">
                          <Checkbox
                            checked={isPairingModeWarningBeforeStashEnabled}
                            onCheckedChange={(checked) =>
                              handleSettingChange(UserConfigField.IS_PAIRING_MODE_WARNING_BEFORE_STASH_ENABLED, checked)
                            }
                          />
                          <Text size="2" color="gray">
                            Show a confirmation dialog before stashing uncommitted changes when starting Pairing Mode.
                          </Text>
                        </Flex>
                      ) : undefined
                    }
                  >
                    <Switch
                      checked={isPairingModeStashingBetaFeatureOn}
                      onCheckedChange={(checked) =>
                        handleSettingChange(UserConfigField.IS_PAIRING_MODE_STASHING_BETA_FEATURE_ON, checked)
                      }
                      variant="soft"
                    />
                  </SettingRow>
                  <HotkeyField
                    title="Global Hotkey"
                    description="Set a system-wide hotkey to show/hide Sculptor from anywhere. When set, Sculptor will stay on top of other windows."
                    value={globalHotkey}
                    onSet={(hotkey) => handleSettingChange(UserConfigField.GLOBAL_HOTKEY, hotkey)}
                    onClear={() => handleSettingChange(UserConfigField.GLOBAL_HOTKEY, "")}
                    elementId={ElementIds.SETTINGS_GLOBAL_HOTKEY_FIELD}
                  />
                  <SettingRow
                    title="Suggestions"
                    description="Suggestions is a feature that will automatically identify issues and suggest improvements to generated code."
                  >
                    <Switch
                      checked={areSuggestionsEnabled}
                      onCheckedChange={(checked) =>
                        handleSettingChange(UserConfigField.ARE_SUGGESTIONS_ENABLED, checked)
                      }
                      variant="soft"
                    />
                  </SettingRow>
                  {areSuggestionsEnabled && (
                    <>
                      <SettingRow
                        title="Suggestions Run Frequency"
                        description="Automatic runs at the end of each turn. Manual runs only when you specify."
                      >
                        <Select.Root
                          value={imbueVerifyRunFrequency}
                          onValueChange={(value) =>
                            handleSettingChange(UserConfigField.IMBUE_VERIFY_RUN_FREQUENCY, value)
                          }
                        >
                          <Select.Trigger variant="soft" className={styles.settingControl} />
                          <Select.Content>
                            <Select.Item value="auto">Automatic</Select.Item>
                            <Select.Item value="manual">Manual</Select.Item>
                          </Select.Content>
                        </Select.Root>
                      </SettingRow>
                      {imbueVerifyRunFrequency === "auto" && (
                        <SettingRow
                          title="Suggestions Token Requirements"
                          description="Minimum coding agent tokens required to trigger suggestion generation."
                        >
                          <Select.Root
                            value={imbueVerifyTokenUsageRequirement}
                            onValueChange={(value) =>
                              handleSettingChange(UserConfigField.IMBUE_VERIFY_TOKEN_USAGE_REQUIREMENT, value)
                            }
                          >
                            <Select.Trigger variant="soft" className={styles.settingControl} />
                            <Select.Content>
                              <Select.Item value="none">None</Select.Item>
                              <Select.Item value="low">Low</Select.Item>
                              <Select.Item value="medium">Medium</Select.Item>
                              <Select.Item value="high">High</Select.Item>
                            </Select.Content>
                          </Select.Root>
                        </SettingRow>
                      )}
                    </>
                  )}
                  {isDevMode && (
                    <SettingRow
                      title="Dev Suggestions"
                      description="Dev Suggestions provides a panel for information and feedback on suggestions."
                    >
                      <Switch
                        checked={areDevSuggestionsOn}
                        onCheckedChange={(checked) =>
                          handleSettingChange(UserConfigField.ARE_DEV_SUGGESTIONS_ON, checked)
                        }
                        variant="soft"
                      />
                    </SettingRow>
                  )}
                  {isDevMode && (
                    <SettingRow
                      title="Scout"
                      description="Enable Scout, a alpha feature for running agentic verification on your code."
                    >
                      <Switch
                        checked={isScoutBetaFeatureOn}
                        onCheckedChange={(checked) =>
                          handleSettingChange(UserConfigField.IS_SCOUT_BETA_FEATURE_ON, checked)
                        }
                        variant="soft"
                      />
                    </SettingRow>
                  )}
                  <SettingRow
                    title="Synchronize Claude Code Configuration"
                    description="Synchronize your local Claude Code configuration (settings.json, subagents and custom slash commands) into the task containers."
                  >
                    <Switch
                      checked={isClaudeConfigurationSynchronized}
                      onCheckedChange={(checked) =>
                        handleSettingChange(UserConfigField.IS_CLAUDE_CONFIGURATION_SYNCHRONIZED, checked)
                      }
                      variant="soft"
                    />
                  </SettingRow>

                  <SettingRow
                    title="Update Channel"
                    description="Choose which update channel to receive application updates from. Alpha updates more frequently with latest features, but may be less stable. Selecting the stable channel will never downgrade your app."
                    data-testid={ElementIds.SETTINGS_USE_INTERNAL_UPDATE_CHANNEL}
                  >
                    <Select.Root value={updateChannel} onValueChange={handleUpdateChannelChange}>
                      <Select.Trigger variant="soft" className={styles.settingControl} />
                      <Select.Content>
                        <Select.Item value="STABLE">Stable</Select.Item>
                        <Select.Item value="ALPHA">Alpha</Select.Item>
                      </Select.Content>
                    </Select.Root>
                  </SettingRow>
                </Flex>
              )}
            </Flex>
          </ScrollArea>
        </Flex>
        <Flex justify="end" mr="5" mb="1" flexShrink="0">
          <VersionDisplay />
        </Flex>
      </Flex>
      <Toast
        open={!!toast}
        onOpenChange={(open) => !open && setToast(null)}
        title={toast?.title}
        description={toast?.description}
        type={toast?.type}
      />
      <AlertDialog.Root open={shouldShowAlphaChannelWarning} onOpenChange={cancelAlphaChannelSwitch}>
        <AlertDialog.Content>
          <AlertDialog.Title>Switch to Alpha Channel?</AlertDialog.Title>
          <AlertDialog.Description>
            <Text size="2">
              Warning: Switching to the Alpha channel might cause breakage and loss of data. Please make sure to join
              the Sculptor Discord at{" "}
              <Link href="https://discord.com/channels/1391837726583820409" target="_blank" rel="noopener noreferrer">
                discord.com/channels/1391837726583820409
              </Link>{" "}
              for tips and tricks and support.
            </Text>
          </AlertDialog.Description>
          <Flex gap="3" justify="end" mt="4">
            <AlertDialog.Cancel>
              <Button variant="soft" color="gray">
                Cancel
              </Button>
            </AlertDialog.Cancel>
            <AlertDialog.Action>
              <Button onClick={confirmAlphaChannelSwitch}>Yes</Button>
            </AlertDialog.Action>
          </Flex>
        </AlertDialog.Content>
      </AlertDialog.Root>
    </>
  );
};
