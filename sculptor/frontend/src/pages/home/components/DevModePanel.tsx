import { Button, Flex, Heading } from "@radix-ui/themes";
import type { ReactElement } from "react";

import styles from "./DevModePanel.module.scss";

export const DevModePanel = (): ReactElement => {
  return (
    <Flex direction="column" gap="4" className={styles.container}>
      {/* Banner */}
      <Flex justify="center" className={styles.banner}>
        <Heading size="4" className={styles.bannerText}>
          super secret area
        </Heading>
      </Flex>

      {/* Control Panel Content */}
      <Flex direction="column" align="start" gap="3" className={styles.controlPanel}>
        <Button
          type="button"
          variant="outline"
          color="red"
          onClick={() => {
            throw new Error("Intentionally raised error for Sentry testing at " + new Date().toISOString());
          }}
          className={styles.testSentryButton}
        >
          Test Sentry Error
        </Button>
        {/* Future development controls can be added here */}
      </Flex>
    </Flex>
  );
};
