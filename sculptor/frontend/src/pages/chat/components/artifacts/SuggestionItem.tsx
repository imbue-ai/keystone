import { Box, Flex, IconButton, Text, Tooltip } from "@radix-ui/themes";
import { Copy as CopyIcon } from "lucide-react";
import type { ReactElement } from "react";
import { useState } from "react";

import { addFix } from "../../../../api";
import { Toast, type ToastContent, ToastType } from "../../../../components/Toast.tsx";
import sharedStyles from "./shared-suggestion-styles.module.scss";
import { handleSuggestionUse } from "./suggestionActions";
import styles from "./SuggestionItem.module.scss";
import type { ProcessedSuggestionWithSource } from "./suggestionUtils";

type SuggestionItemProps = {
  suggestion: ProcessedSuggestionWithSource;
  onUse?: () => void;
  appendTextRef?: React.MutableRefObject<((text: string) => void) | null>;
  hasBeenHovered?: boolean;
  onFirstHover?: () => void;
  projectID?: string;
  taskID?: string;
};

export const SuggestionItem = ({
  suggestion,
  onUse,
  appendTextRef,
  hasBeenHovered = false,
  onFirstHover,
  projectID,
  taskID,
}: SuggestionItemProps): ReactElement => {
  const [isHovered, setIsHovered] = useState(false);
  const [toast, setToast] = useState<ToastContent | null>(null);

  const handleClick = async (): Promise<void> => {
    handleSuggestionUse(suggestion.suggestion.description, suggestion.suggestion.actions, appendTextRef);
    onUse?.();

    if (projectID && taskID) {
      try {
        await addFix({
          path: { project_id: projectID, task_id: taskID },
          body: { description: suggestion.suggestion.description },
        });
      } catch (err) {
        console.error("Error calling addFix:", err);
      }
    }
  };

  const handleMouseEnter = (): void => {
    setIsHovered(true);
    if (!hasBeenHovered) {
      onFirstHover?.();
    }
  };

  const handleMouseLeave = (): void => {
    setIsHovered(false);
  };

  const handleCopyDescription = async (e: React.MouseEvent): Promise<void> => {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(suggestion.suggestion.description);
      setToast({ title: "Description copied to clipboard", type: ToastType.SUCCESS });
    } catch (err) {
      console.error("Failed to copy description:", err);
      setToast({ title: "Failed to copy description", type: ToastType.ERROR });
    }
  };

  return (
    <>
      <Tooltip
        content={
          <Box className={styles.tooltipContent} data-suggestion-tooltip="true">
            <Flex justify="between" align="center" mb="1">
              <Text weight="medium" size="2" className={styles.tooltipHeader}>
                Description
              </Text>
              <IconButton size="1" variant="ghost" onClick={handleCopyDescription} className={styles.copyButton}>
                <CopyIcon />
              </IconButton>
            </Flex>
            <Text size="2" className={styles.tooltipDescription}>
              {suggestion.suggestion.description}
            </Text>
          </Box>
        }
        side="right"
        align="start"
        sideOffset={18}
        delayDuration={hasBeenHovered ? 0 : 500}
      >
        <Box
          className={`${sharedStyles.suggestionItem} ${sharedStyles.topSuggestionItem} ${styles.suggestionItemWrapper}`}
          onClick={handleClick}
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
        >
          <Flex align="center" justify="between" py="1" px="2">
            <Text size="2" className={`${styles.suggestionTitle} ${isHovered ? styles.hovered : ""}`}>
              {suggestion.suggestion.title.charAt(0).toUpperCase() + suggestion.suggestion.title.slice(1)}
            </Text>

            {isHovered && (
              <Text size="1" className={styles.clickToUseText}>
                Click to use
              </Text>
            )}
          </Flex>
        </Box>
      </Tooltip>
      <Toast open={!!toast} onOpenChange={(open) => !open && setToast(null)} title={toast?.title} type={toast?.type} />
    </>
  );
};
