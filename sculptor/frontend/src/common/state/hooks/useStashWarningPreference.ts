import { useAtomValue } from "jotai";

import { UserConfigField } from "~/api";

import { isPairingModeWarningBeforeStashEnabledAtom } from "../atoms/userConfig.ts";
import { useUserConfig } from "./useUserConfig.ts";

type StashWarningPreferenceControls = {
  isStashWarningEnabled: boolean;
  disableStashWarning: () => Promise<void>;
};

export const useStashWarningPreference = (): StashWarningPreferenceControls => {
  const isStashWarningEnabled = useAtomValue(isPairingModeWarningBeforeStashEnabledAtom);
  const { updateField } = useUserConfig();

  const disableStashWarning = async (): Promise<void> => {
    try {
      await updateField(UserConfigField.IS_PAIRING_MODE_WARNING_BEFORE_STASH_ENABLED, false);
    } catch (error) {
      console.error("Failed to update stash warning preference:", error);
    }
  };

  return {
    isStashWarningEnabled,
    disableStashWarning,
  };
};
