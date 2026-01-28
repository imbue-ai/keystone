import * as Dialog from "@radix-ui/react-dialog";
import { Box, Button, Checkbox, Flex, Link, Spinner, Text, TextField, Tooltip } from "@radix-ui/themes";
import { CircleCheckIcon, CircleDashedIcon } from "lucide-react";
import type React from "react";
import { type ReactElement, useEffect, useState } from "react";

import { HTTPException, ValidationError } from "~/common/Errors.ts";
import { AnthropicAuthButton } from "~/components/AnthropicAuthButton.tsx";
import { BackgroundProjectLayout } from "~/components/BackgroundProjectLayout";
import { CredentialsManager } from "~/components/CredentialsManager.tsx";

import type { DependenciesStatus } from "../api";
import { completeOnboarding, ElementIds, getDependenciesStatus, savePrivacySettings, saveUserEmail } from "../api";
import { updateTelemetryConfig } from "../common/Telemetry";
import styles from "./OnboardingWizard.module.scss";
import type { TelemetryLevel } from "./TelemetrySettingsSelector.tsx";
import { TelemetrySettingsSelector } from "./TelemetrySettingsSelector.tsx";
import { TitleBar } from "./TitleBar";

export const OnboardingStep = {
  EMAIL: "EMAIL",
  INSTALLATION: "INSTALLATION",
} as const;

export type OnboardingStep = (typeof OnboardingStep)[keyof typeof OnboardingStep];

type OnboardingWizardProps = {
  initialStep: OnboardingStep;
  onComplete: () => void;
};

export const OnboardingWizard = ({ initialStep, onComplete }: OnboardingWizardProps): ReactElement => {
  const [currentStep, setCurrentStep] = useState<OnboardingStep>(initialStep);
  const [isLoading, setIsLoading] = useState(false);
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState<string | null>(null);
  const [didOptInToMarketing, setDidOptInToMarketing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleEmailSubmit = async (
    email: string,
    fullName: string | null,
    didOptInToMarketing: boolean,
  ): Promise<void> => {
    setIsLoading(true);
    setError(null);

    try {
      console.log("Saving user email:", email, fullName, "Marketing opt-in:", didOptInToMarketing);
      const { data: updatedTelemetryInfo } = await saveUserEmail({
        body: {
          userEmail: email,
          fullName: fullName,
          didOptInToMarketing: didOptInToMarketing,
        },
        meta: { skipWsAck: true },
      });
      if (updatedTelemetryInfo) {
        updateTelemetryConfig(updatedTelemetryInfo);
      }
      setEmail(email);
      setFullName(fullName);
      setDidOptInToMarketing(didOptInToMarketing);
      setCurrentStep(OnboardingStep.INSTALLATION);
    } catch (err) {
      let errorMessage = "Failed to save email";
      if (err instanceof ValidationError) {
        errorMessage = err.detail[0].msg;
      } else if (err instanceof Error) {
        errorMessage = err.message;
      }
      setError(errorMessage);
    } finally {
      setIsLoading(false);
    }
  };

  const handleBack = (): void => {
    setCurrentStep(OnboardingStep.EMAIL);
  };

  const handleInstallationComplete = async (data: {
    telemetryLevel: number;
    isRepoBackupEnabled: boolean;
  }): Promise<void> => {
    setIsLoading(true);
    setError(null);

    try {
      await savePrivacySettings({
        body: {
          telemetryLevel: data.telemetryLevel,
          isRepoBackupEnabled: data.isRepoBackupEnabled,
        },
        meta: { skipWsAck: true },
      });

      await completeOnboarding({
        meta: { skipWsAck: true },
      });

      onComplete();
    } catch (error) {
      let errorMessage = "Failed to complete onboarding";
      if (error instanceof HTTPException) {
        errorMessage = error.detail;
      } else if (error instanceof Error) {
        errorMessage = error.message;
      }
      setError(errorMessage);
    } finally {
      setIsLoading(false);
    }
  };

  if (currentStep === OnboardingStep.EMAIL) {
    return (
      <WelcomeStepComponent
        onNext={handleEmailSubmit}
        isLoading={isLoading}
        error={error}
        initialEmail={email}
        initialFullName={fullName}
        initialDidOptInToMarketing={didOptInToMarketing}
      />
    );
  }

  return (
    <InstallationStepComponent
      onComplete={handleInstallationComplete}
      isLoading={isLoading}
      error={error}
      onBack={handleBack}
    />
  );
};

type WelcomeStepComponentProps = {
  onNext: (email: string, fullName: string | null, didOptInToMarketing: boolean) => void;
  isLoading: boolean;
  error: string | null;
  initialEmail: string;
  initialFullName: string | null;
  initialDidOptInToMarketing: boolean;
};

const WelcomeStepComponent = ({
  onNext,
  isLoading,
  error,
  initialEmail,
  initialFullName,
  initialDidOptInToMarketing,
}: WelcomeStepComponentProps): ReactElement => {
  const [email, setEmail] = useState(initialEmail);
  const [fullName, setFullName] = useState(initialFullName);
  const [didOptInToMarketing, setDidOptInToMarketing] = useState(initialDidOptInToMarketing);

  const handleSubmit = (): void => {
    if (email && email.includes("@")) {
      onNext(email, fullName || null, didOptInToMarketing);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent): void => {
    if (e.key === "Enter") {
      handleSubmit();
    }
  };

  return (
    <>
      <BackgroundProjectLayout />

      <Dialog.Root open onOpenChange={() => {}}>
        <Dialog.Overlay className={styles.overlay}>
          <TitleBar></TitleBar>
        </Dialog.Overlay>
        <Dialog.Content
          className={styles.welcomeDialog}
          onEscapeKeyDown={(e) => e.preventDefault()}
          onPointerDownOutside={(e) => e.preventDefault()}
          onInteractOutside={(e) => e.preventDefault()}
          data-testid={ElementIds.ONBOARDING_WELCOME_STEP}
        >
          <Flex direction="column" style={{ height: "100%" }} mb="0" p="6" align="center">
            <Flex direction="column" gap="1" align="center">
              <Dialog.Title>
                <Text className={styles.titleText}>Start building with Sculptor</Text>
              </Dialog.Title>
            </Flex>
            <Flex direction="column" mt="auto" mb="auto" gap="2" width="360px">
              <TextField.Root
                placeholder="Full name"
                size="3"
                value={fullName || ""}
                onChange={(e) => setFullName(e.target.value)}
                onKeyDown={handleKeyPress}
                className={styles.nameInput}
                data-testid={ElementIds.ONBOARDING_FULL_NAME_INPUT}
              />
              <TextField.Root
                placeholder="Email address"
                size="3"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                onKeyDown={handleKeyPress}
                className={styles.emailInput}
                data-testid={ElementIds.ONBOARDING_EMAIL_INPUT}
              />
              {error && (
                <Text size="2" color="red" className={styles.error}>
                  {error}
                </Text>
              )}
              <Button
                size="3"
                mb="2"
                variant="solid"
                onClick={handleSubmit}
                disabled={isLoading || !email || !email.includes("@")}
                className={styles.primaryButton}
                data-testid={ElementIds.ONBOARDING_EMAIL_SUBMIT}
              >
                {isLoading ? <Spinner /> : "Get Started"}
              </Button>
              <Text as="label" size="2">
                <Flex direction="column" align="start">
                  <Flex gap="2" align="center">
                    <Checkbox
                      checked={didOptInToMarketing}
                      onCheckedChange={(checked) => setDidOptInToMarketing(checked === true)}
                      data-testid={ElementIds.ONBOARDING_MARKETING_CHECKBOX}
                    />
                    <Text size="2" color="gray">
                      Receive product update emails (optional)
                    </Text>
                  </Flex>
                </Flex>
              </Text>
            </Flex>

            <Flex className={styles.tosAndPrivacy} align="center" justify="center" gap="0" direction="column">
              {/* Terms and Conditions */}
              <Text size="2" color="gray" align="center" className={styles.secondaryText}>
                By continuing, you agree to our{" "}
              </Text>
              <Text>
                <Link href="https://imbue.com/terms" className={styles.termsText}>
                  Terms of Service
                </Link>{" "}
                and{" "}
                <Link href="https://imbue.com/privacy" className={styles.termsText}>
                  Privacy Policy
                </Link>
              </Text>
            </Flex>
          </Flex>
        </Dialog.Content>
      </Dialog.Root>
    </>
  );
};

export type LLMProviderConfigBoxProps = {
  hasAnyCredentials: boolean;
  setHasAnyCredentials: (value: boolean) => void;
};

// This is used to allow either OpenAI or Anthropic key to pass through the onboarding. We currently use
// AnthropicOnlyProviderConfigBox, but we should use LLMProviderConfigBox instead soon.
export const LLMProviderConfigBox = ({
  hasAnyCredentials,
  setHasAnyCredentials,
}: LLMProviderConfigBoxProps): ReactElement => {
  return (
    <Flex direction="row" justify="between" align="center" gapX="4">
      {hasAnyCredentials ? (
        <CircleCheckIcon className={styles.circleIcon} />
      ) : (
        <CircleDashedIcon className={styles.circleIcon} />
      )}

      <Flex direction="column" gap="2" style={{ flex: 1 }}>
        <Text className={styles.primaryText}>Configure AI Provider</Text>
        <span>
          <Text className={styles.secondaryText}>Sculptor needs access to at least one AI provider. You can use </Text>
          <Link href="https://docs.anthropic.com/en/api/overview" className={styles.linkText}>
            Anthropic
          </Link>
          <Text className={styles.secondaryText}> for Claude agents or </Text>
          <Link href="https://platform.openai.com/api-keys" className={styles.linkText}>
            OpenAI
          </Link>
          <Text className={styles.secondaryText}> for Codex agents</Text>
        </span>
      </Flex>

      {hasAnyCredentials ? (
        <Text className={styles.secondaryText}>Configured</Text>
      ) : (
        <CredentialsManager onCredentialsChange={setHasAnyCredentials} compact />
      )}
    </Flex>
  );
};

const AnthropicOnlyProviderConfigBox = ({
  hasAnyCredentials,
  setHasAnyCredentials,
}: LLMProviderConfigBoxProps): ReactElement => {
  return (
    <Flex direction="row" justify="between" align="center" gapX="4">
      {hasAnyCredentials ? (
        <CircleCheckIcon className={styles.circleIcon} />
      ) : (
        <CircleDashedIcon className={styles.circleIcon} />
      )}
      <Flex direction="column" gap="2" style={{ flex: 1 }}>
        <Text className={styles.primaryText}>Configure Anthropic</Text>
        <span>
          <Text className={styles.secondaryText}>Sculptor uses your Anthropic </Text>
          <Link href="https://docs.anthropic.com/en/api/overview" className={styles.linkText}>
            API key
          </Link>
          <Text className={styles.secondaryText}> or account to run Claude Code under the hood</Text>
        </span>
      </Flex>
      {hasAnyCredentials ? (
        <Text className={styles.secondaryText}>Access granted</Text>
      ) : (
        <AnthropicAuthButton onAuthStatusChange={setHasAnyCredentials} />
      )}
    </Flex>
  );
};

type InstallationStepComponentProps = {
  onComplete: (data: { telemetryLevel: number; isRepoBackupEnabled: boolean }) => void;
  isLoading: boolean;
  error: string | null;
  onBack: () => void;
};

/** The InstallationStepComponent is the second step of the OnboardingWizard where we verify that users have
 * the necessary dependencies installed.
 *
 * It is a key requirement of this page to track the appropriate PostHog events granurlaly as users complete the verious
 * steps so that we can identify where users are dropping off in the onboarding process.
 */
const InstallationStepComponent = ({
  onComplete,
  isLoading,
  error,
  onBack,
}: InstallationStepComponentProps): ReactElement => {
  const [telemetryLevel, setTelemetryLevel] = useState<TelemetryLevel>("4");
  // TODO: figure out how we will configure repo backups

  const [isRepoBackupEnabled, setIsRepoBackupEnabled] = useState(false);
  const [dependencies, setDependencies] = useState<DependenciesStatus | null>(null);
  const [isDependenciesLoading, setIsDependenciesLoading] = useState(true);
  const [dependenciesError, setDependenciesError] = useState<string | null>(null);
  const [hasAnyCredentials, setHasAnyCredentials] = useState(false);

  const loadDependencies = async (): Promise<void> => {
    try {
      setIsDependenciesLoading(true);
      const { data: dependenciesStatus } = await getDependenciesStatus();
      setDependencies(dependenciesStatus);
    } catch (err) {
      let errorMessage = "Failed to complete onboarding";
      if (err instanceof HTTPException) {
        errorMessage = err.detail;
      } else if (err instanceof Error) {
        errorMessage = err.message;
      }
      setDependenciesError(errorMessage);
      setDependencies(null);
      console.error("Failed to load dependencies:", errorMessage);
    } finally {
      setIsDependenciesLoading(false);
    }
  };

  // I want to run this effect every 30 seconds to re-check the dependencies
  // However, I never want to run it if loadDependencies is already running.
  useEffect(() => {
    loadDependencies();
  }, []);

  useEffect(() => {
    const interval = setInterval((): void => {
      if (!isDependenciesLoading) {
        loadDependencies();
      }
    }, 30_000);

    return (): void => clearInterval(interval); // Cleanup on unmount
  }, [isDependenciesLoading]);

  /* We can only submit if all the dependencies are installed and at least one AI provider is configured */
  const canSubmit = (): boolean => {
    return (
      !isLoading &&
      !isDependenciesLoading &&
      hasAnyCredentials &&
      dependencies !== null &&
      Object.values(dependencies).every((v) => v === true)
    );
  };

  const handleSubmit = (): void => {
    // We cannot enable repo backups from this screen yet.
    setIsRepoBackupEnabled(false);

    if (telemetryLevel) {
      onComplete({
        telemetryLevel: parseInt(telemetryLevel),
        isRepoBackupEnabled,
      });
    }
  };

  return (
    <Flex
      align="center"
      direction="column"
      justify="center"
      className={styles.container}
      data-testid={ElementIds.ONBOARDING_INSTALLATION_STEP}
    >
      <TitleBar />
      <Box width="var(--main-content-width)">
        <Flex direction="column" gap="2">
          <Text className={styles.titleText}>Let&apos;s get you set up</Text>
          <Text color="gray" className={styles.secondaryText}>
            The following are required to use Sculptor
          </Text>

          {/* Dependencies Section */}
          <Flex direction="column" gap="3">
            <>
              {/* Configure AI Provider */}
              <Box className={hasAnyCredentials ? styles.completeDependencyCard : styles.dependencyCard} p="5">
                <AnthropicOnlyProviderConfigBox
                  hasAnyCredentials={hasAnyCredentials}
                  setHasAnyCredentials={setHasAnyCredentials}
                />
              </Box>

              {/* Install Docker */}
              <Box
                className={dependencies?.dockerRunning ? styles.completeDependencyCard : styles.dependencyCard}
                p="5"
                data-testid={ElementIds.ONBOARDING_DOCKER_CARD}
              >
                <Flex direction="row" justify="between" align="center" gapX="4">
                  {dependencies?.dockerRunning ? (
                    <CircleCheckIcon className={styles.circleIcon} />
                  ) : (
                    <CircleDashedIcon className={styles.circleIcon} />
                  )}
                  <Flex direction="column" gap="2" style={{ flex: 1 }}>
                    <Text className={styles.primaryText}>Install and Start Docker</Text>
                    <span>
                      <Text className={styles.secondaryText}>
                        Add safety to your LLM based workflow by running everything inside of{" "}
                      </Text>
                      <Link href="https://www.docker.com/" className={styles.linkText}>
                        Docker
                      </Link>
                    </span>
                  </Flex>
                  <Flex direction="column" align="center" gapY="3" data-testid={ElementIds.ONBOARDING_DOCKER_STATUS}>
                    {dependencies?.dockerRunning ? (
                      <Text className={styles.secondaryText}>Running</Text>
                    ) : dependencies?.dockerInstalled ? (
                      <Tooltip content="Ensure Docker is running" delayDuration={200}>
                        <Button variant="soft" onClick={() => false} disabled>
                          Launch Docker
                        </Button>
                      </Tooltip>
                    ) : (
                      <Button variant="soft" onClick={() => window.open("https://docs.docker.com/get-docker/")}>
                        Install
                      </Button>
                    )}
                  </Flex>
                </Flex>
              </Box>

              {/* Install Git */}
              <Box
                className={dependencies?.gitInstalled ? styles.completeDependencyCard : styles.dependencyCard}
                p="5"
                data-testid={ElementIds.ONBOARDING_GIT_CARD}
              >
                <Flex direction="row" justify="between" align="center" gapX="4">
                  {dependencies?.gitInstalled ? (
                    <CircleCheckIcon className={styles.circleIcon} />
                  ) : (
                    <CircleDashedIcon className={styles.circleIcon} />
                  )}
                  <Flex direction="column" gap="2" style={{ flex: 1 }}>
                    <Text className={styles.primaryText}>Install Git</Text>
                    <Text className={styles.secondaryText}>
                      We use Git to version control and sync changes made to files
                    </Text>
                  </Flex>
                  <Flex direction="column" align="center" gapY="3" data-testid={ElementIds.ONBOARDING_GIT_STATUS}>
                    {dependencies?.gitInstalled ? (
                      <Text className={styles.secondaryText}>Installed</Text>
                    ) : (
                      <Button variant="soft" onClick={() => window.open("https://git-scm.com/downloads")}>
                        Install
                      </Button>
                    )}
                  </Flex>
                </Flex>
              </Box>

              {/* Telemetry Settings */}
              <Box className={styles.completeDependencyCard} p="5">
                <Flex direction="row" justify="between" align="center" gapX="4">
                  <Flex direction="row" justify="between" align="center" gapX="5">
                    <CircleCheckIcon className={styles.circleIcon} />
                  </Flex>
                  <Flex direction="column" gap="2" style={{ flex: 1 }}>
                    <Text className={styles.primaryText}>Usage Data & Privacy</Text>
                    <Text className={styles.secondaryText}>
                      Control what data Sculptor collects to improve it for you and others. Change this in Settings.
                    </Text>
                  </Flex>
                  <Box className={styles.telemetrySettings}>
                    <TelemetrySettingsSelector value={telemetryLevel} onValueChange={setTelemetryLevel} />
                  </Box>
                </Flex>
              </Box>
            </>
          </Flex>

          {error && (
            <Text size="2" color="red" className={styles.error}>
              {error}
            </Text>
          )}

          {dependenciesError && (
            <Text size="2" color="red" className={styles.error}>
              {dependenciesError}
            </Text>
          )}

          <Flex p="1" gap="3" justify="end" align="center">
            <Button variant="soft" size="3" onClick={onBack} data-testid={ElementIds.ONBOARDING_BACK_BUTTON}>
              Back
            </Button>
            {canSubmit() ? (
              <Button
                size="3"
                variant="solid"
                onClick={handleSubmit}
                className={styles.primaryButton}
                data-testid={ElementIds.ONBOARDING_COMPLETE_BUTTON}
              >
                Continue
              </Button>
            ) : (
              <Button
                size="3"
                variant="solid"
                disabled={isDependenciesLoading}
                className={styles.primaryButtonDisabled}
                data-testid={ElementIds.ONBOARDING_COMPLETE_BUTTON}
                onClick={loadDependencies}
              >
                <Flex className={styles.buttonInner}>
                  <Flex className={isDependenciesLoading ? styles.labelHidden : undefined}>Check Now</Flex>
                  {isDependenciesLoading && (
                    <Flex className={styles.spinnerOverlay} align="center" justify="center" aria-hidden="true">
                      <Spinner />
                    </Flex>
                  )}
                </Flex>
              </Button>
            )}
          </Flex>
        </Flex>
      </Box>
    </Flex>
  );
};
