import { useAtomValue } from "jotai";
import type { ReactElement, ReactNode } from "react";
import { useEffect, useRef } from "react";

import { globalHotkeyAtom } from "../common/state/atoms/userConfig.ts";
import { useUserConfig } from "../common/state/hooks/useUserConfig.ts";

type ConfigLoaderProps = {
  children: ReactNode;
  isTokenReady: boolean;
};

/**
 * ConfigLoader Component
 *
 * Loads user configuration once the token is ready.
 * This ensures we have the user's settings (theme, shortcuts, etc.)
 * loaded before the main app components try to use them.
 */
export const ConfigLoader = ({ children, isTokenReady }: ConfigLoaderProps): ReactElement => {
  const { loadConfig } = useUserConfig();
  const globalHotkey = useAtomValue(globalHotkeyAtom);
  const hasRegisteredInitialHotkey = useRef(false);

  // Load config when token is ready
  useEffect(() => {
    if (isTokenReady) {
      loadConfig();
    }
  }, [isTokenReady, loadConfig]);

  // Register initial global hotkey once on startup
  useEffect(() => {
    if (globalHotkey && !hasRegisteredInitialHotkey.current && window.sculptor?.setGlobalHotkey) {
      hasRegisteredInitialHotkey.current = true;
      console.log(`Registering initial global hotkey: ${globalHotkey}`);
      window.sculptor.setGlobalHotkey(globalHotkey).then((result) => {
        if (result.success) {
          console.log(`Initial global hotkey registered: ${globalHotkey}`);
        } else {
          console.error(`Failed to register initial global hotkey: ${result.error}`);
        }
      });
    }
  }, [globalHotkey]);

  return <>{children}</>;
};
