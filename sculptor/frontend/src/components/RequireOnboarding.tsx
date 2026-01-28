import { Spinner } from "@radix-ui/themes";
import type { ReactElement, ReactNode } from "react";
import { useEffect, useState } from "react";

import { getConfigStatus } from "../api";
import { OnboardingStep, OnboardingWizard } from "./OnboardingWizard";

type RequireOnboardingProps = {
  children: ReactNode;
  isTokenReady: boolean;
};

export const RequireOnboarding = ({ children, isTokenReady }: RequireOnboardingProps): ReactElement => {
  const [isCheckingConfig, setIsCheckingConfig] = useState(false);
  const [isOnboardingComplete, setIsOnboardingComplete] = useState(false);
  const [currentOnboardingStep, setCurrentOnboardingStep] = useState<OnboardingStep>(OnboardingStep.EMAIL);

  // Check config status to determine if onboarding is needed
  useEffect(() => {
    const checkConfigStatus = async (): Promise<void> => {
      if (!isTokenReady) {
        return;
      }
      setIsCheckingConfig(true);
      try {
        const { data: configStatus } = await getConfigStatus({
          meta: { skipWsAck: true },
        });

        const isComplete =
          configStatus.hasEmail &&
          configStatus.hasApiKey &&
          configStatus.hasPrivacyConsent &&
          configStatus.hasTelemetryLevel;

        if (isComplete) {
          setIsOnboardingComplete(true);
        } else {
          if (!configStatus.hasEmail) {
            setCurrentOnboardingStep(OnboardingStep.EMAIL);
          } else {
            setCurrentOnboardingStep(OnboardingStep.INSTALLATION);
          }
          setIsOnboardingComplete(false);
        }
      } catch (error) {
        console.error("Failed to check config status:", error);
        // If config check fails, assume onboarding is needed
        setIsOnboardingComplete(false);
        setCurrentOnboardingStep(OnboardingStep.EMAIL);
      }
      setIsCheckingConfig(false);
    };

    checkConfigStatus();
  }, [isTokenReady]);

  const handleOnboardingComplete = (): void => {
    setIsOnboardingComplete(true);
  };

  if (isTokenReady && isCheckingConfig) {
    return <Spinner />;
  }

  if (isTokenReady && !isCheckingConfig && !isOnboardingComplete) {
    return <OnboardingWizard initialStep={currentOnboardingStep} onComplete={handleOnboardingComplete} />;
  }

  if (isTokenReady && isOnboardingComplete) {
    return <>{children}</>;
  }

  // Don't render anything if token is not ready
  return <></>;
};
