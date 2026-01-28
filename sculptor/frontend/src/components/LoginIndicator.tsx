import { ChevronDownIcon } from "@radix-ui/react-icons";
import { DropdownMenu, Flex, IconButton, Spinner, Text } from "@radix-ui/themes";
import { useAtom } from "jotai";
import type { ReactElement } from "react";
import { useEffect, useState } from "react";

import type { UserInfo } from "../api";
import { currentUser } from "../api";
import { forgetTokens, isLoggingOutAtom, LOGOUT_ENDPOINT } from "../common/Auth";

export const LoginIndicator = (): ReactElement | undefined => {
  const [userInfo, setUserInfo] = useState<UserInfo | undefined>(undefined);
  const [isLoggingOut, setIsLoggingOut] = useAtom(isLoggingOutAtom);

  const fetchUserInfo = async (): Promise<void> => {
    try {
      const { data: userInfo } = await currentUser();

      if (userInfo) {
        setUserInfo(userInfo);
      }
    } catch (error) {
      console.error("Failed to fetch user info:", error);
      return;
    }
  };

  useEffect(() => {
    fetchUserInfo();
  }, []);

  const logout = (): void => {
    setIsLoggingOut(true);
    forgetTokens();
    window.location.href = LOGOUT_ENDPOINT;
  };

  if (!userInfo) {
    // Only show the login indicator if the user is logged in.
    return undefined;
  }

  return (
    <Flex align="center" gap="2">
      <Text size="2">{userInfo.email}</Text>

      <DropdownMenu.Root>
        <DropdownMenu.Trigger>
          <IconButton variant="ghost" size="1" aria-label="Open menu">
            <ChevronDownIcon />
          </IconButton>
        </DropdownMenu.Trigger>

        <DropdownMenu.Content sideOffset={5} align="end">
          <DropdownMenu.Item onSelect={logout} disabled={isLoggingOut}>
            <Flex align="center" gap="2">
              {isLoggingOut && <Spinner size="1" />}
              <Text>Log out</Text>
            </Flex>
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Root>
    </Flex>
  );
};
