import { Box, Button, Flex, Text, TextField } from "@radix-ui/themes";
import type React from "react";
import { type ReactElement, useEffect, useRef, useState } from "react";

import styles from "./AccountFieldRow.module.scss";

type AccountFieldRowProps = {
  title: string;
  description: string;
  value: string;
  isEditing?: boolean;
  onEdit: () => void;
  onSave: (value: string) => void;
  onCancel: () => void;
  readOnly?: boolean;
  elementId?: string;
  inputTestId?: string;
  saveButtonTestId?: string;
  editButtonTestId?: string;
};

export const AccountFieldRow = ({
  title,
  description,
  value,
  isEditing = false,
  onEdit,
  onSave,
  onCancel,
  readOnly = false,
  elementId,
  inputTestId,
  saveButtonTestId,
  editButtonTestId,
}: AccountFieldRowProps): ReactElement => {
  const [editValue, setEditValue] = useState(value);
  const inputRef = useRef<HTMLInputElement>(null);
  const saveButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (isEditing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [isEditing]);

  const handleSave = (): void => {
    onSave(editValue);
  };

  const handleBlur = (e: React.FocusEvent): void => {
    // Don't cancel if focus moved to the save button
    if (e.relatedTarget === saveButtonRef.current) {
      return;
    }
    setEditValue(value);
    onCancel();
  };

  return (
    <Flex direction="column" width="100%" py="4" className={styles.settingRow} data-testid={elementId}>
      <Text weight="medium">{title}</Text>
      <Flex justify="between" align="center" gapX="8">
        <Text size="2" className={styles.descriptionText}>
          {description}
        </Text>
        {isEditing ? (
          <Box>
            <TextField.Root
              ref={inputRef}
              value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              onBlur={handleBlur}
              className={styles.textField}
              data-testid={inputTestId}
            >
              <TextField.Slot side="right">
                <Button
                  ref={saveButtonRef}
                  variant="solid"
                  size="1"
                  onClick={handleSave}
                  data-testid={saveButtonTestId}
                >
                  Save
                </Button>
              </TextField.Slot>
            </TextField.Root>
          </Box>
        ) : (
          <Flex
            className={readOnly ? styles.readOnlyFieldNonEditable : styles.readOnlyField}
            align="center"
            onClick={!readOnly ? onEdit : undefined}
            data-testid={editButtonTestId}
          >
            <Text size="2">{value}</Text>
          </Flex>
        )}
      </Flex>
    </Flex>
  );
};
