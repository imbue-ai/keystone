import { ChevronDownIcon, ChevronLeftIcon, ChevronRightIcon } from "@radix-ui/react-icons";
import { Box, Button, Checkbox, DropdownMenu, Flex, IconButton, Text, Tooltip } from "@radix-ui/themes";
import { useAtom, useAtomValue } from "jotai";
import { Copy as CopyIcon, Lightbulb } from "lucide-react";
import { type ReactElement, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { addFix, ArtifactType } from "../../../../api";
import { useTaskPageParams } from "../../../../common/NavigateUtils.ts";
import { isRightPanelOpenAtom } from "../../../../common/state/atoms/sidebar.ts";
import { navigateToMostRecentSuggestionsTurnAtomFamily } from "../../../../common/state/atoms/tasks.ts";
import { useIsNarrowLayout } from "../../../../common/state/hooks/useComponentWidthById.ts";
import { useStrictTask } from "../../../../common/state/hooks/useTaskHelpers.ts";
import { Toast, type ToastContent, ToastType } from "../../../../components/Toast.tsx";
import type { ArtifactViewContentProps, ArtifactViewTabLabelProps } from "../../Types.ts";
import { CheckStatusDisplay, getCheckStatusDisplay } from "../../utils/checkStatusUtils";
import { extractUseActionContent, handleSuggestionUse } from "./suggestionActions";
import styles from "./SuggestionsArtifactView.module.scss";
import {
  filterSuggestionsFromCheckOutputs,
  getSuggestionsForMessage,
  type ProcessedSuggestionWithSource,
} from "./suggestionUtils";
import { useSuggestionsForMessage } from "./useSuggestions.ts";

type SuggestionItemProps = {
  suggestion: ProcessedSuggestionWithSource;
  suggestionKey: string;
  isSelected: boolean;
  onToggle: (suggestionKey: string) => void;
  appendTextRef?: React.MutableRefObject<((text: string) => void) | null>;
  hasBeenHovered?: boolean;
  onFirstHover?: () => void;
  isCompactMode?: boolean;
};

const DetailedSuggestionItem = ({
  suggestion,
  suggestionKey,
  isSelected,
  onToggle,
  appendTextRef,
  hasBeenHovered = false,
  onFirstHover,
  isCompactMode = false,
}: SuggestionItemProps): ReactElement => {
  const [toast, setToast] = useState<ToastContent | null>(null);
  const { projectID, taskID } = useTaskPageParams();

  const handleCheckboxChange = (_checked: boolean | "indeterminate"): void => {
    onToggle(suggestionKey);
  };

  const handleMouseEnter = (): void => {
    if (!hasBeenHovered) {
      onFirstHover?.();
    }
  };

  const handleCopyDescription = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(suggestion.suggestion.description);
      setToast({ title: "Description copied to clipboard", type: ToastType.SUCCESS });
    } catch (err) {
      console.error("Failed to copy description:", err);
      setToast({ title: "Failed to copy description", type: ToastType.ERROR });
    }
  };

  const handleUse = async (): Promise<void> => {
    handleSuggestionUse(suggestion.suggestion.description, suggestion.suggestion.actions, appendTextRef);

    if (isSelected) {
      onToggle(suggestionKey);
    }

    try {
      await addFix({
        path: { project_id: projectID, task_id: taskID },
        body: { description: suggestion.suggestion.description },
      });
    } catch (err) {
      console.error("Error calling addFix:", err);
    }
  };

  return (
    <Box className={styles.suggestionItem}>
      <Tooltip
        content={
          <Box
            style={{
              maxWidth: isCompactMode ? "90vw" : "500px",
              padding: "8px",
              userSelect: "text",
            }}
            data-suggestion-tooltip="true"
          >
            <Flex justify="between" align="center" mb="1">
              <Text weight="medium" size="2" style={{ display: "block", color: "var(--gold-12)", cursor: "text" }}>
                Description
              </Text>
              <IconButton
                size="1"
                variant="ghost"
                onClick={handleCopyDescription}
                style={{ cursor: "pointer", color: "var(--gold-9)" }}
              >
                <CopyIcon style={{ width: "14px", height: "14px" }} />
              </IconButton>
            </Flex>
            <Text
              size="2"
              style={{ color: "var(--gold-11)", cursor: "text", whiteSpace: "pre-wrap", wordBreak: "break-word" }}
            >
              {suggestion.suggestion.description}
            </Text>
          </Box>
        }
        side={isCompactMode ? "top" : "right"}
        align="start"
        sideOffset={isCompactMode ? 5 : 18}
        delayDuration={hasBeenHovered ? 0 : 500}
      >
        <Flex align="center" gap="3" py="1" px="2" onMouseEnter={handleMouseEnter}>
          <Flex
            align="center"
            gap="3"
            style={{ flex: 1, minWidth: 0, cursor: "default" }}
            className={styles.clickableArea}
          >
            <Checkbox checked={isSelected} onCheckedChange={handleCheckboxChange} />

            <Flex direction="column" gap="2" style={{ flex: 1, minWidth: 0 }}>
              <Text
                weight="medium"
                size="2"
                className={styles.suggestionTitle}
                style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}
              >
                {suggestion.suggestion.title.charAt(0).toUpperCase() + suggestion.suggestion.title.slice(1)}
              </Text>
            </Flex>
          </Flex>

          <Flex gap="4" align="center">
            <Button size="1" onClick={handleUse} className={styles.useButton}>
              Use
            </Button>
          </Flex>
        </Flex>
      </Tooltip>

      <Toast open={!!toast} onOpenChange={(open) => !open && setToast(null)} title={toast?.title} type={toast?.type} />
    </Box>
  );
};

const IMBUE_VERIFY_SERVER_INFO_KEY = "imbue" as const;
type VerifyStatus = "loading" | "error" | "connected" | "building" | "empty";

const useVerifyMCPStatus = (): VerifyStatus => {
  const { taskID } = useTaskPageParams();
  const task = useStrictTask(taskID);

  const servers = task.mcpServers;

  if (task.status === "BUILDING") {
    return "building";
  }

  if (!servers || Object.keys(servers).length === 0) {
    return "empty";
  }

  const serverInfo = servers[IMBUE_VERIFY_SERVER_INFO_KEY];
  if (!serverInfo || serverInfo.status !== "connected") {
    return "error";
  }

  return "connected";
};

const getErrorTextFromVerifyStatus = (status: VerifyStatus): string => {
  if (status === "building") {
    return "Waiting for the container to be ready";
  } else if (status === "empty") {
    // TODO (PROD-2217): On restart, we lose the imbue-cli process in the container and just display this
    return "Waiting for Imbue verify to be ready";
  } else {
    return "Imbue verify is offline";
  }
};

export const SuggestionsViewComponent = ({
  artifacts,
  userMessageIds,
  appendTextRef,
  checksData,
}: ArtifactViewContentProps): ReactElement => {
  const { projectID, taskID } = useTaskPageParams();
  const status = useVerifyMCPStatus();
  const navigateToMostRecentTurnTrigger = useAtomValue(navigateToMostRecentSuggestionsTurnAtomFamily(taskID));
  const [selectedSuggestions, setSelectedSuggestions] = useState<Set<string>>(new Set());
  const [currentMessageIdIndex, setCurrentMessageIdIndex] = useState(0);
  const hasInitialized = useRef(false);
  const [toast, setToast] = useState<ToastContent | null>(null);
  const previousMessageIdsLength = useRef(0);
  const previousTaskIdRef = useRef(taskID);
  const [hasBeenHovered, setHasBeenHovered] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const [isRightPanelOpen] = useAtom(isRightPanelOpenAtom);
  const isNarrowLayout = useIsNarrowLayout();
  const isCompactMode = isNarrowLayout || !isRightPanelOpen;

  // Use the provided user message IDs
  const availableMessageIds = useMemo(() => userMessageIds || [], [userMessageIds]);

  const suggestionsData = filterSuggestionsFromCheckOutputs(artifacts[ArtifactType.NEW_CHECK_OUTPUTS]);

  const messageIdsWithSuggestions = useMemo(() => {
    const set = new Set<string>();
    for (const messageId of availableMessageIds) {
      const suggestions = getSuggestionsForMessage(suggestionsData, messageId);
      if (suggestions.length > 0) {
        set.add(messageId);
      }
    }
    return set;
  }, [availableMessageIds, suggestionsData]);

  // Function to get a meaningful label for a message ID
  const getMessageLabel = useCallback(
    (messageId: string, index: number): string => {
      const hasSuggestions = messageIdsWithSuggestions.has(messageId);
      return `Turn ${index + 1}${hasSuggestions ? " *" : ""}`;
    },
    [messageIdsWithSuggestions],
  );

  useEffect(() => {
    if (taskID !== previousTaskIdRef.current) {
      hasInitialized.current = false;
      previousMessageIdsLength.current = 0;
      setCurrentMessageIdIndex(0);
      previousTaskIdRef.current = taskID;
    }

    if (availableMessageIds.length > 0 && !hasInitialized.current) {
      setCurrentMessageIdIndex(availableMessageIds.length - 1);
      previousMessageIdsLength.current = availableMessageIds.length;
      hasInitialized.current = true;
    }
  }, [availableMessageIds, taskID]);

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

  useEffect(() => {
    if (navigateToMostRecentTurnTrigger > 0 && availableMessageIds.length > 0) {
      setCurrentMessageIdIndex(availableMessageIds.length - 1);
    }
  }, [navigateToMostRecentTurnTrigger, availableMessageIds.length]);

  const currentMessageId = availableMessageIds[currentMessageIdIndex];

  useEffect(() => {
    setSelectedSuggestions(new Set());
  }, [currentMessageId]);
  const getAllSuggestions = useSuggestionsForMessage(suggestionsData, currentMessageId);

  const getSuggestionKey = useCallback((suggestion: ProcessedSuggestionWithSource): string => {
    return `${suggestion.userMessageId}-${suggestion.checkName}-${suggestion.runId}-${suggestion.suggestion.title}-${suggestion.suggestion.description}`;
  }, []);

  const handleToggleSelection = useCallback((suggestionKey: string): void => {
    setSelectedSuggestions((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(suggestionKey)) {
        newSet.delete(suggestionKey);
      } else {
        newSet.add(suggestionKey);
      }
      return newSet;
    });
  }, []);

  const handleUseAllSelected = useCallback(async (): Promise<void> => {
    if (selectedSuggestions.size === 0) return;

    const selectedSuggestionsArray = getAllSuggestions.filter((suggestion) =>
      selectedSuggestions.has(getSuggestionKey(suggestion)),
    );

    const selectedTexts = selectedSuggestionsArray
      .map((suggestion) => extractUseActionContent(suggestion.suggestion.description, suggestion.suggestion.actions))
      .filter(Boolean)
      .join("\n\n");

    if (appendTextRef?.current) {
      appendTextRef.current("\n" + selectedTexts + "\n");
      setToast({ title: "All text appended to message box", type: ToastType.SUCCESS });
    } else {
      navigator.clipboard.writeText(selectedTexts);
      setToast({ title: "All use information copied to clipboard", type: ToastType.SUCCESS });
    }

    setSelectedSuggestions(new Set());

    try {
      for (const suggestion of selectedSuggestionsArray) {
        await addFix({
          path: { project_id: projectID, task_id: taskID },
          body: { description: suggestion.suggestion.description },
        });
      }
    } catch (err) {
      console.error("Error calling addFix for multiple items:", err);
    }
  }, [selectedSuggestions, getAllSuggestions, projectID, taskID, appendTextRef, getSuggestionKey]);

  const isImbueVerifyRunningForCurrentTurn = useMemo(() => {
    if (!checksData || !currentMessageId) return false;

    const imbueVerifyCheck = checksData[currentMessageId]?.["Verifier"];
    if (!imbueVerifyCheck) return false;

    const latestRunId = imbueVerifyCheck.runIds?.[imbueVerifyCheck.runIds.length - 1];
    const latestStatus = latestRunId ? imbueVerifyCheck.statusByRunId[latestRunId] : null;
    const checkStatus = getCheckStatusDisplay(latestStatus);

    return checkStatus === CheckStatusDisplay.RUNNING;
  }, [checksData, currentMessageId]);

  const hasImbueVerifyRunForCurrentTurn = useMemo(() => {
    if (!checksData || !currentMessageId) return false;
    const imbueVerifyCheck = checksData[currentMessageId]?.["Verifier"];
    return imbueVerifyCheck && imbueVerifyCheck.runIds && imbueVerifyCheck.runIds.length > 0;
  }, [checksData, currentMessageId]);

  const handleSelectAll = useCallback((): void => {
    const allKeys = new Set(getAllSuggestions.map((suggestion) => getSuggestionKey(suggestion)));
    setSelectedSuggestions(allKeys);
  }, [getAllSuggestions, getSuggestionKey]);

  const handleDeselectAll = (): void => {
    setSelectedSuggestions(new Set());
  };

  if (status === "error" || status === "building") {
    return (
      <>
        <Flex direction="column" gap="1" maxWidth="100%" className={styles.suggestionsContainer}>
          <Flex className={styles.noSuggestions} justify="center" align="center" p="3">
            <Text color="gray">{getErrorTextFromVerifyStatus(status)}</Text>
          </Flex>
        </Flex>
        <Toast
          open={!!toast}
          onOpenChange={(open) => !open && setToast(null)}
          title={toast?.title}
          type={toast?.type}
        />
      </>
    );
  }

  return (
    <>
      <Flex
        key={currentMessageId}
        direction="column"
        gap="1"
        maxWidth="100%"
        className={styles.suggestionsContainer}
        ref={containerRef}
        onMouseLeave={() => setHasBeenHovered(false)}
      >
        <Flex direction="column" gap="3" className={styles.suggestionsHeader}>
          <Flex gap="2" align="center" justify="between">
            <Flex gap="2" align="center" className={styles.navigationControls}>
              <Button
                size="1"
                variant="ghost"
                className={styles.navButton}
                onClick={() => setCurrentMessageIdIndex(Math.max(0, currentMessageIdIndex - 1))}
                disabled={currentMessageIdIndex === 0}
              >
                <ChevronLeftIcon />
              </Button>
              <div className={styles.dropdownSpacer}></div>
              <DropdownMenu.Root>
                <DropdownMenu.Trigger>
                  <Button size="1" variant="ghost" className={styles.navDropdownButton}>
                    {getMessageLabel(availableMessageIds[currentMessageIdIndex], currentMessageIdIndex)}
                    <ChevronDownIcon className={styles.dropdownIcon} />
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
              <div className={styles.dropdownSpacer}></div>
              <Button
                size="1"
                variant="ghost"
                className={styles.navButton}
                onClick={() =>
                  setCurrentMessageIdIndex(Math.min(availableMessageIds.length - 1, currentMessageIdIndex + 1))
                }
                disabled={currentMessageIdIndex === availableMessageIds.length - 1}
              >
                <ChevronRightIcon />
              </Button>
            </Flex>

            <Flex gap="2" align="center">
              {selectedSuggestions.size > 0 && (
                <Button size="1" onClick={handleUseAllSelected} className={styles.useButton}>
                  Use Selected ({selectedSuggestions.size})
                </Button>
              )}
              <Button
                size="1"
                onClick={selectedSuggestions.size === 0 ? handleSelectAll : handleDeselectAll}
                className={styles.selectAllButton}
                disabled={selectedSuggestions.size === 0 && getAllSuggestions.length === 0}
              >
                {selectedSuggestions.size === 0 ? "Select All" : "Deselect All"}
              </Button>
            </Flex>
          </Flex>
        </Flex>

        {getAllSuggestions.length === 0 ? (
          <Flex className={styles.noSuggestions} justify="center" align="center" p="3">
            <Text color="gray">
              {isImbueVerifyRunningForCurrentTurn
                ? "Running Verifier..."
                : hasImbueVerifyRunForCurrentTurn
                  ? "No suggestions for this turn"
                  : "Verifier not run for this turn"}
            </Text>
          </Flex>
        ) : (
          <>
            {getAllSuggestions.map((suggestion) => {
              const suggestionKey = getSuggestionKey(suggestion);
              return (
                <DetailedSuggestionItem
                  key={suggestionKey}
                  suggestion={suggestion}
                  suggestionKey={suggestionKey}
                  isSelected={selectedSuggestions.has(suggestionKey)}
                  onToggle={handleToggleSelection}
                  appendTextRef={appendTextRef}
                  hasBeenHovered={hasBeenHovered}
                  onFirstHover={() => setHasBeenHovered(true)}
                  isCompactMode={isCompactMode}
                />
              );
            })}
            {isImbueVerifyRunningForCurrentTurn && (
              <Flex className={styles.generatingBanner} justify="center" align="center" p="2">
                <Text size="2" style={{ color: "var(--gold-11)" }}>
                  Running Verifier...
                </Text>
              </Flex>
            )}
          </>
        )}
      </Flex>
      <Toast open={!!toast} onOpenChange={(open) => !open && setToast(null)} title={toast?.title} type={toast?.type} />
    </>
  );
};

export const SuggestionsTabLabelComponent = ({ shouldShowIcon = true }: ArtifactViewTabLabelProps): ReactElement => {
  const component = useMemo((): ReactElement => {
    return (
      <Flex align="center" gap="2">
        {shouldShowIcon && <Lightbulb className={styles.lightbulbIcon} />}
        Suggestions
      </Flex>
    );
  }, [shouldShowIcon]);

  return component;
};
