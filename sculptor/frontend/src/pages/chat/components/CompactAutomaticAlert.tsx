import { Box, Button, Flex, Text } from "@radix-ui/themes";
import type { ReactElement } from "react";

import styles from "./CompactAutomaticAlert.module.scss";

type CompactAutomaticAlertProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCompactClick: () => void;
  tokenInfoText: string;
  disabled: boolean;
};

export const CompactAutomaticAlert = ({
  open,
  onOpenChange,
  onCompactClick,
  tokenInfoText,
  disabled,
}: CompactAutomaticAlertProps): ReactElement => {
  if (!open) return <></>;

  return (
    <Box className={styles.overlay} position="fixed" top="0" left="0" right="0" bottom="0">
      <Flex className={styles.alertDialog} width="400px" height="240px" direction="column">
        <Flex className={styles.textGroup} direction="column">
          <Text className={styles.headerText}>
            You are reaching the end of your context window. Please compact your context.
          </Text>
          <Text className={styles.warningText}>
            You cannot undo this action. This will compact the context from this session into a summary.
          </Text>
          <Text className={styles.tokenInfoText}>{tokenInfoText}</Text>
        </Flex>
        <Flex className={styles.buttonRow} direction="row" justify="end">
          <Button variant="soft" color="gray" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            color="red"
            disabled={disabled}
            onClick={() => {
              onCompactClick();
              onOpenChange(false);
            }}
          >
            Yes, Compact
          </Button>
        </Flex>
      </Flex>
    </Box>
  );
};
