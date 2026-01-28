import { useAtomValue } from "jotai";

import { sculptorSettingsAtom } from "../atoms/sculptorSettings.ts";

export const useForkFeatureFlag = (): boolean => {
  const settings = useAtomValue(sculptorSettingsAtom);
  return !!settings?.IS_FORKING_ENABLED;
};

export const useNewManualSyncFeatureFlag = (): boolean => {
  return false; // Feature not available yet
};
