import { Dialog, Flex } from "@radix-ui/themes";
import { useAtomValue, useSetAtom } from "jotai";
import type { ReactElement } from "react";
import { useEffect, useRef, useState } from "react";

import { authRequiredModalAtom, authStore, getLoginURL, isLoggingOutAtom } from "../common/Auth.ts";
import { PendingButton } from "./PendingButton.tsx";

export const AuthRequiredModal = (): ReactElement | undefined => {
  const isOpen = useAtomValue(authRequiredModalAtom, { store: authStore });
  const isLoggingOut = useAtomValue(isLoggingOutAtom);
  const setIsOpen = useSetAtom(authRequiredModalAtom, { store: authStore });
  const [isLoggingIn, setIsLoggingIn] = useState<boolean>(false);
  const buttonRef = useRef<HTMLButtonElement>(null);

  const handleLoginClick = async (): Promise<void> => {
    setIsLoggingIn(true);
    window.location.href = getLoginURL();
    // Keep the spinner running until the user is redirected.
    return new Promise(() => {});
  };

  useEffect(() => {
    // Automatically click the button for now.
    // (Eventually, there will be situations when we'll want to let users close the dialog instead.)
    if (isOpen) {
      // Use a zero timeout to ensure the button is already mounted.
      const timer = setTimeout((): void => {
        if (buttonRef.current) {
          buttonRef.current.click();
        }
      }, 0);
      return (): void => clearTimeout(timer);
    }
  }, [isOpen]);

  if (isLoggingOut) {
    // If the user is currently logging out, we don't show the dialog.
    return undefined;
  }

  return (
    <Dialog.Root
      open={isOpen}
      onOpenChange={(open) => {
        // Don't allow the user to close the dialog.
        if (open) setIsOpen(true);
      }}
    >
      <Dialog.Content>
        <Flex direction="column" gap="3">
          <Dialog.Title>{isLoggingIn ? "Logging you in..." : "Login required"}</Dialog.Title>
          <Dialog.Description>{isLoggingIn ? "Please wait" : "You're currently not logged in."}</Dialog.Description>
          <PendingButton onClick={handleLoginClick} ref={buttonRef}>
            {isLoggingIn ? "Logging in..." : "Login"}
          </PendingButton>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
};
