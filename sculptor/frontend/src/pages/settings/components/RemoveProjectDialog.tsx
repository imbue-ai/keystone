import { Box, Button, Dialog, Flex, Spinner, Text } from "@radix-ui/themes";
import type { ReactElement } from "react";

import styles from "./RemoveProjectDialog.module.scss";

type RemoveProjectDialogProps = {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: () => void;
  projectName: string;
  activeAgentCount: number;
  archivedAgentCount: number;
  isDeleting: boolean;
};

export const RemoveProjectDialog = ({
  isOpen,
  onClose,
  onConfirm,
  projectName,
  activeAgentCount,
  archivedAgentCount,
  isDeleting,
}: RemoveProjectDialogProps): ReactElement => {
  return (
    <Dialog.Root open={isOpen} onOpenChange={onClose}>
      <Dialog.Content className={styles.dialogContent}>
        <Dialog.Title>Remove Repository</Dialog.Title>

        <Flex direction="column" gap="4">
          <Text size="2">
            Are you sure you want to remove <strong>{projectName}</strong> and all of the associated agents from
            Sculptor? This action cannot be undone.
          </Text>

          <Box className={styles.agentCountsBox}>
            <Text size="2">
              {activeAgentCount} active agent{activeAgentCount !== 1 ? "s" : ""}, {archivedAgentCount} archived
            </Text>
          </Box>

          <Flex gap="3" className={styles.actions} justify="end">
            <Dialog.Close>
              <Button variant="soft" color="gray" disabled={isDeleting}>
                Cancel
              </Button>
            </Dialog.Close>
            <Button variant="solid" color="red" onClick={onConfirm} disabled={isDeleting} style={{ minWidth: "185px" }}>
              {isDeleting ? <Spinner /> : "Remove repo & agents"}
            </Button>
          </Flex>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
};
