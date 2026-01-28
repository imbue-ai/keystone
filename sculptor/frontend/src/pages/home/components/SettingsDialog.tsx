import { Button, Dialog, Flex, Text, TextArea } from "@radix-ui/themes";
import type { ReactElement } from "react";
import { useState } from "react";

import { ElementIds } from "../../../api";
import styles from "./SettingsDialog.module.scss";

type SettingsDialogProps = {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  defaultSystemPrompt: string | null;
  onSave: (prompt: string) => Promise<void>;
};

export const SettingsDialog = ({
  isOpen,
  onOpenChange,
  defaultSystemPrompt,
  onSave,
}: SettingsDialogProps): ReactElement => {
  const [localDefaultPrompt, setLocalDefaultPrompt] = useState<string | null>(defaultSystemPrompt);

  const handleSave = async (): Promise<void> => {
    if (localDefaultPrompt === null) {
      return;
    }
    await onSave(localDefaultPrompt);
  };

  const handleCancel = (): void => {
    onOpenChange(false);
  };

  return (
    <Dialog.Root open={isOpen} onOpenChange={onOpenChange}>
      <Dialog.Content className={styles.dialogContent}>
        <Flex direction="column" gapY="3">
          <Text as="div" size="3" mb="1" weight="bold">
            Default System Prompt
          </Text>
          <TextArea
            value={localDefaultPrompt || ""}
            onChange={(e) => setLocalDefaultPrompt(e.target.value)}
            placeholder="Enter default system prompt for new tasks..."
            rows={8}
            data-testid={ElementIds.HOME_PAGE_SYSTEM_PROMPT_INPUT}
          />
        </Flex>

        <Flex gap="3" mt="4" justify="end">
          <Dialog.Close>
            <Button variant="soft" color="gray" onClick={handleCancel}>
              Cancel
            </Button>
          </Dialog.Close>
          <Button onClick={handleSave} data-testid={ElementIds.HOME_PAGE_SYSTEM_PROMPT_SAVE_BUTTON}>
            Save
          </Button>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
};
