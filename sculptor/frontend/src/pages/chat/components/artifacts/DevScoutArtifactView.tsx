import { ChevronDownIcon, ChevronLeftIcon, ChevronRightIcon } from "@radix-ui/react-icons";
import { Box, Button, DropdownMenu, Flex, Text, Tooltip } from "@radix-ui/themes";
import { ShieldCheck } from "lucide-react";
import { type ReactElement, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ArtifactType } from "../../../../api/index.ts";
import { Toast, type ToastContent } from "../../../../components/Toast.tsx";
import type { ArtifactViewContentProps, ArtifactViewTabLabelProps } from "../../Types.ts";
import styles from "./DevScoutArtifactView.module.scss";
import { filterScoutOutputsFromCheckOutputs, getScoutOutputForMessage } from "./scoutUtils.ts";

// DevScoutArtifactView: a panel for viewing detailed info on scout outputs and recording notes on them to posthog
export const DevScoutArtifactViewComponent = ({
  artifacts,
  userMessageIds,
}: ArtifactViewContentProps): ReactElement => {
  const [toast, setToast] = useState<ToastContent | null>(null);
  const [currentMessageIdIndex, setCurrentMessageIdIndex] = useState(0);
  const hasInitialized = useRef(false);
  const previousMessageIdsLength = useRef(0);

  const availableMessageIds = useMemo(() => userMessageIds || [], [userMessageIds]);

  // get suggestions from artifacts (same as regular Suggestions pane)
  const scoutOutputsData = filterScoutOutputsFromCheckOutputs(artifacts[ArtifactType.NEW_CHECK_OUTPUTS]);

  const getMessageLabel = useCallback((messageId: string, index: number): string => {
    return `Turn ${index + 1}`;
  }, []);

  // initialize to most recent turn
  useEffect(() => {
    if (availableMessageIds.length > 0 && !hasInitialized.current) {
      setCurrentMessageIdIndex(availableMessageIds.length - 1);
      previousMessageIdsLength.current = availableMessageIds.length;
      hasInitialized.current = true;
    }
  }, [availableMessageIds]);

  // auto-advance to new turns
  useEffect(() => {
    if (!hasInitialized.current) return;

    const newLength = availableMessageIds.length;
    const oldLength = previousMessageIdsLength.current;

    if (newLength > oldLength) {
      const isViewingMostRecent = currentMessageIdIndex === oldLength - 1;

      if (isViewingMostRecent) {
        setCurrentMessageIdIndex(newLength - 1);
      }
    }

    previousMessageIdsLength.current = newLength;
  }, [availableMessageIds.length, currentMessageIdIndex]);

  const currentMessageId = availableMessageIds[currentMessageIdIndex];

  // get scout outputs for current turn only
  const currentTurnScoutOutputs = useMemo(() => {
    if (!currentMessageId) {
      return [];
    }
    return getScoutOutputForMessage(scoutOutputsData, currentMessageId);
  }, [currentMessageId, scoutOutputsData]);

  return (
    <>
      <Flex direction="column" gap="4" p="4" className={styles.container}>
        <Flex direction="column" gap="4" className={styles.formSection}>
          <Box>
            <Flex justify="between" align="center" mb="2">
              <Text size="2" weight="medium" as="div">
                Available Scout Outputs ({currentTurnScoutOutputs.length})
              </Text>
              {availableMessageIds.length > 1 && (
                <Flex gap="2" align="center" className={styles.navigationControls}>
                  <Button
                    size="1"
                    variant="ghost"
                    onClick={() => setCurrentMessageIdIndex(Math.max(0, currentMessageIdIndex - 1))}
                    disabled={currentMessageIdIndex === 0}
                  >
                    <ChevronLeftIcon />
                  </Button>
                  <DropdownMenu.Root>
                    <DropdownMenu.Trigger>
                      <Button size="1" variant="ghost">
                        {getMessageLabel(availableMessageIds[currentMessageIdIndex], currentMessageIdIndex)}
                        <ChevronDownIcon />
                      </Button>
                    </DropdownMenu.Trigger>
                    <DropdownMenu.Content size="1">
                      {availableMessageIds.map((messageId, index) => (
                        <DropdownMenu.Item key={messageId} onClick={() => setCurrentMessageIdIndex(index)}>
                          {getMessageLabel(messageId, index)}
                        </DropdownMenu.Item>
                      ))}
                    </DropdownMenu.Content>
                  </DropdownMenu.Root>
                  <Button
                    size="1"
                    variant="ghost"
                    onClick={() =>
                      setCurrentMessageIdIndex(Math.min(availableMessageIds.length - 1, currentMessageIdIndex + 1))
                    }
                    disabled={currentMessageIdIndex === availableMessageIds.length - 1}
                  >
                    <ChevronRightIcon />
                  </Button>
                </Flex>
              )}
            </Flex>
            <Box className={styles.scoutOutputsListContainer}>
              {currentTurnScoutOutputs.length === 0 ? (
                <Text size="2" color="gray" as="div">
                  No scout outputs available
                </Text>
              ) : (
                <Flex direction="column" gap="1">
                  {currentTurnScoutOutputs.map((scoutOutput) => (
                    <Tooltip
                      key={scoutOutput.output.id}
                      content={
                        <Box className={styles.tooltipContent} data-scout-json-tooltip="true">
                          <Text weight="medium" size="2" as="div" className={styles.tooltipTitle}>
                            Scout Output JSON
                          </Text>
                          <pre className={styles.tooltipCode}>{JSON.stringify(scoutOutput.output, null, 2)}</pre>
                        </Box>
                      }
                      side="right"
                      align="start"
                      sideOffset={5}
                      delayDuration={200}
                    >
                      <Flex className={styles.scoutOutputListItem} p="2" justify="between" align="center" gap="2">
                        <Text size="2" className={styles.scoutOutputTitle} as="span">
                          {scoutOutput.output.objectType}
                        </Text>
                      </Flex>
                    </Tooltip>
                  ))}
                </Flex>
              )}
            </Box>
          </Box>
        </Flex>
      </Flex>
      <Toast open={!!toast} onOpenChange={(open) => !open && setToast(null)} title={toast?.title} type={toast?.type} />
    </>
  );
};

export const DevScoutTabLabelComponent = ({ shouldShowIcon = true }: ArtifactViewTabLabelProps): ReactElement => {
  const component = useMemo((): ReactElement => {
    return (
      <Flex align="center" gap="2">
        {shouldShowIcon && <ShieldCheck size={16} />}
        Dev Scout
      </Flex>
    );
  }, [shouldShowIcon]);

  return component;
};
