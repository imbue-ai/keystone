import { Box, Flex } from "@radix-ui/themes";
import { useAtomValue } from "jotai";
import type { ReactElement } from "react";

import { isSidebarOpenAtom } from "../../common/state/atoms/sidebar.ts";
import { LoginIndicator } from "../../components/LoginIndicator.tsx";
import { TitleBar } from "../../components/TitleBar.tsx";
import { VersionDisplay } from "../../components/VersionDisplay.tsx";
import { CreateTaskForm } from "./components/CreateTaskForm.tsx";
import { ProjectHeader } from "./components/ProjectHeader.tsx";
import styles from "./HomePage.module.scss";

export const HomePage = (): ReactElement => {
  const isSidebarOpen = useAtomValue(isSidebarOpenAtom);

  return (
    <Flex width="100%" height="100%" direction="column" className={styles.container} position="relative">
      <TitleBar shouldShowToggleSidebarButton={!isSidebarOpen} />
      <Flex
        direction="column"
        width="calc(min(var(--main-content-width), 80%))"
        height="100%"
        mx="auto"
        justify="center"
        align="center"
      >
        <Flex direction="column" gap="1" width="100%">
          <Box height="70px">
            <ProjectHeader />
          </Box>
          <Flex justify="between" align="center">
            <LoginIndicator />
          </Flex>
          <CreateTaskForm />
        </Flex>
      </Flex>
      <Flex justify="end" mr="5" mb="1">
        <VersionDisplay />
      </Flex>
    </Flex>
  );
};
