import { ChevronDownIcon, ChevronLeftIcon, ChevronRightIcon } from "@radix-ui/react-icons";
import {
  Badge,
  Box,
  Button,
  DropdownMenu,
  Flex,
  IconButton,
  Text,
  TextArea,
  TextField,
  Tooltip,
} from "@radix-ui/themes";
import { Copy, MessageSquare, ThumbsDown, ThumbsUp, X } from "lucide-react";
import { posthog } from "posthog-js";
import { type ReactElement, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ArtifactType } from "../../../../api";
import { useTaskPageParams } from "../../../../common/NavigateUtils.ts";
import { Toast, type ToastContent, ToastType } from "../../../../components/Toast";
import type { ArtifactViewContentProps, ArtifactViewTabLabelProps } from "../../Types.ts";
import styles from "./DevSuggestionsArtifactView.module.scss";
import { filterSuggestionsFromCheckOutputs, getAllSuggestionsForMessages } from "./suggestionUtils";

// generate a unique key for a suggestion
const generateSuggestionKey = (suggestion: {
  userMessageId: string;
  checkName: string;
  runId: string;
  suggestion: { title: string };
}): string => {
  return `${suggestion.userMessageId}-${suggestion.checkName}-${suggestion.runId}-${suggestion.suggestion.title}`;
};

const showToast = (setToast: (toast: ToastContent | null) => void, title: string, type: ToastType): void => {
  setToast({ title, type });
};

// DevSuggestionsArtifactView: a panel for viewing detailed info on suggestions and recording notes on them to posthog
export const DevSuggestionsArtifactViewComponent = ({
  artifacts,
  userMessageIds,
}: ArtifactViewContentProps): ReactElement => {
  const { taskID } = useTaskPageParams();
  const [suggestionId, setSuggestionId] = useState<string>("");
  const [feedbackMessage, setFeedbackMessage] = useState<string>("");
  const [toast, setToast] = useState<ToastContent | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [currentMessageIdIndex, setCurrentMessageIdIndex] = useState(0);
  const [thumbsRating, setThumbsRating] = useState<"up" | "down" | null>(null);
  const [tags, setTags] = useState<Array<string>>([]);
  const [tagInput, setTagInput] = useState<string>("");
  const hasInitialized = useRef(false);
  const previousMessageIdsLength = useRef(0);

  const availableMessageIds = useMemo(() => userMessageIds || [], [userMessageIds]);

  // get suggestions from artifacts (same as regular Suggestions pane)
  const suggestionsData = filterSuggestionsFromCheckOutputs(artifacts[ArtifactType.NEW_CHECK_OUTPUTS]);
  const apiSuggestions = getAllSuggestionsForMessages(suggestionsData, availableMessageIds);

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

  // get suggestions for current turn only
  const currentTurnSuggestions = useMemo(() => {
    if (!currentMessageId) {
      return [];
    }
    return apiSuggestions
      .filter((s) => s.userMessageId === currentMessageId)
      .map((s) => ({
        ...s,
        key: generateSuggestionKey(s),
      }));
  }, [currentMessageId, apiSuggestions]);

  // get all suggestions across all turns of this task for raw json display
  const allSuggestions = useMemo(() => {
    return apiSuggestions.map((s) => ({
      ...s,
      key: generateSuggestionKey(s),
    }));
  }, [apiSuggestions]);

  const handleCopyId = async (id: string): Promise<void> => {
    try {
      await navigator.clipboard.writeText(id);
      showToast(setToast, "ID copied to clipboard", ToastType.SUCCESS);
    } catch (error) {
      console.error("Failed to copy ID:", error);
      showToast(setToast, "Failed to copy ID", ToastType.ERROR);
    }
  };

  const handleAddTag = (): void => {
    if (tagInput.trim() && !tags.includes(tagInput.trim())) {
      setTags([...tags, tagInput.trim()]);
      setTagInput("");
    }
  };

  const handleTagInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>): void => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleAddTag();
    }
  };

  const handleRemoveTag = (tagToRemove: string): void => {
    setTags(tags.filter((tag) => tag !== tagToRemove));
  };

  const handleCopySourcePath = async (): Promise<void> => {
    try {
      // construct the path to the suggestions source directory: /agent/data/{taskID}/checks/{userMessageId}/Verifier/{runId}/
      let userMessageId = "unknown";
      let runId = "unknown";
      if (allSuggestions.length > 0) {
        userMessageId = allSuggestions[0].userMessageId || "unknown";
        runId = allSuggestions[0].runId || "unknown";
      }

      const sourcePath = `/agent/data/${taskID}/checks/${userMessageId}/Verifier/${runId}/`;

      await navigator.clipboard.writeText(sourcePath);
      showToast(setToast, "Source path copied to clipboard", ToastType.SUCCESS);
    } catch (error) {
      console.error("Failed to copy source path:", error);
      showToast(setToast, "Failed to copy source path", ToastType.ERROR);
    }
  };

  const handleSubmit = async (): Promise<void> => {
    if (!suggestionId.trim()) {
      showToast(setToast, "Please provide a suggestion ID", ToastType.ERROR);
      return;
    }

    // allow empty feedback message if there's a rating or tags
    if (!feedbackMessage.trim() && !thumbsRating && tags.length === 0) {
      showToast(setToast, "Please provide feedback message, rating, or tags", ToastType.ERROR);
      return;
    }

    setIsSubmitting(true);

    try {
      // Find the suggestion with this ID
      const suggestion = allSuggestions.find((s) => s.key === suggestionId.trim());

      if (!suggestion) {
        showToast(setToast, "Suggestion ID not foun in this task", ToastType.ERROR);
        return;
      }

      // Send to PostHog
      posthog.capture("suggestion_feedback", {
        suggestion_id: suggestionId.trim(),
        suggestion_title: suggestion.suggestion.title,
        suggestion_description: suggestion.suggestion.description,
        suggestion_severity: suggestion.suggestion.severity,
        suggestion_confidence: suggestion.suggestion.confidence,
        check_name: suggestion.checkName,
        run_id: suggestion.runId,
        user_message_id: suggestion.userMessageId,
        feedback_message: feedbackMessage.trim(),
        thumbs_rating: thumbsRating,
        tags: tags,
        timestamp: new Date().toISOString(),
      });

      showToast(setToast, "Feedback submitted", ToastType.SUCCESS);

      // Clear form
      setSuggestionId("");
      setFeedbackMessage("");
      setThumbsRating(null);
      setTags([]);
      setTagInput("");
    } catch (error) {
      console.error("Error submitting feedback:", error);
      showToast(setToast, "Failed to submit feedback", ToastType.ERROR);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <>
      <Flex direction="column" gap="4" p="4" className={styles.container}>
        <Flex direction="column" gap="4" className={styles.formSection}>
          <Box>
            <Flex justify="between" align="center" mb="2">
              <Text size="2" weight="medium" as="div">
                Available Suggestions ({currentTurnSuggestions.length})
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
            <Box className={styles.suggestionsListContainer}>
              {currentTurnSuggestions.length === 0 ? (
                <Text size="2" color="gray" as="div">
                  No suggestions available
                </Text>
              ) : (
                <Flex direction="column" gap="1">
                  {currentTurnSuggestions.map((suggestion) => (
                    <Tooltip
                      key={suggestion.key}
                      content={
                        <Box
                          style={{
                            maxWidth: "600px",
                            maxHeight: "400px",
                            overflow: "auto",
                            padding: "8px",
                            userSelect: "text",
                          }}
                          data-suggestion-json-tooltip="true"
                        >
                          <Text
                            weight="medium"
                            size="2"
                            as="div"
                            style={{ marginBottom: "8px", cursor: "text", color: "var(--gold-12)" }}
                          >
                            Suggestion JSON
                          </Text>
                          <pre
                            style={{
                              margin: 0,
                              fontFamily: "'Monaco', 'Menlo', 'Ubuntu Mono', monospace",
                              fontSize: "11px",
                              lineHeight: "1.4",
                              cursor: "text",
                              whiteSpace: "pre-wrap",
                              wordBreak: "break-word",
                              color: "var(--gold-11)",
                            }}
                          >
                            {JSON.stringify(suggestion, null, 2)}
                          </pre>
                        </Box>
                      }
                      side="right"
                      align="start"
                      sideOffset={5}
                      delayDuration={200}
                    >
                      <Flex className={styles.suggestionListItem} p="2" justify="between" align="center" gap="2">
                        <Text size="2" className={styles.suggestionTitle} as="span">
                          {suggestion.suggestion.title}
                        </Text>
                        <IconButton
                          size="1"
                          variant="ghost"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleCopyId(suggestion.key);
                          }}
                          className={styles.copyButton}
                        >
                          <Copy size={14} />
                        </IconButton>
                      </Flex>
                    </Tooltip>
                  ))}
                </Flex>
              )}
            </Box>
          </Box>

          <Box>
            <Text size="2" weight="medium" mb="2" as="div">
              Suggestion ID
            </Text>
            <TextField.Root
              placeholder="Paste suggestion ID from list above"
              value={suggestionId}
              onChange={(e) => setSuggestionId(e.target.value)}
              size="2"
            />
          </Box>

          <Box>
            <Text size="2" weight="medium" mb="2" as="div">
              Rating
            </Text>
            <Flex gap="2">
              <IconButton
                size="2"
                variant={thumbsRating === "up" ? "solid" : "soft"}
                color={thumbsRating === "up" ? "green" : "gray"}
                onClick={() => setThumbsRating(thumbsRating === "up" ? null : "up")}
              >
                <ThumbsUp size={16} />
              </IconButton>
              <IconButton
                size="2"
                variant={thumbsRating === "down" ? "solid" : "soft"}
                color={thumbsRating === "down" ? "red" : "gray"}
                onClick={() => setThumbsRating(thumbsRating === "down" ? null : "down")}
              >
                <ThumbsDown size={16} />
              </IconButton>
            </Flex>
          </Box>

          <Box>
            <Text size="2" weight="medium" mb="2" as="div">
              Tags
            </Text>
            {tags.length > 0 && (
              <Flex gap="2" mb="2" wrap="wrap">
                {tags.map((tag) => (
                  <Badge key={tag} size="2" variant="soft" className={styles.tagBadge}>
                    {tag}
                    <IconButton
                      size="1"
                      variant="ghost"
                      onClick={() => handleRemoveTag(tag)}
                      className={styles.tagRemoveButton}
                    >
                      <X size={12} />
                    </IconButton>
                  </Badge>
                ))}
              </Flex>
            )}
            <TextField.Root
              placeholder="Add a tag and press enter"
              value={tagInput}
              onChange={(e) => setTagInput(e.target.value)}
              onKeyDown={handleTagInputKeyDown}
              size="2"
            />
          </Box>

          <Box>
            <Text size="2" weight="medium" mb="2" as="div">
              Feedback Message
            </Text>
            <TextArea
              placeholder="Enter your feedback about this suggestion..."
              value={feedbackMessage}
              onChange={(e) => setFeedbackMessage(e.target.value)}
              rows={4}
              size="2"
            />
          </Box>

          <Button
            size="2"
            onClick={handleSubmit}
            disabled={
              isSubmitting || !suggestionId.trim() || (!feedbackMessage.trim() && !thumbsRating && tags.length === 0)
            }
          >
            {isSubmitting ? "Sending..." : "Submit Feedback"}
          </Button>

          <Box>
            <Text size="2" weight="medium" mb="2" as="div">
              Source JSON File
            </Text>
            <Button size="2" variant="outline" onClick={handleCopySourcePath}>
              <Copy size={14} />
              Copy Source Path
            </Button>
          </Box>
        </Flex>
      </Flex>

      <Toast open={!!toast} onOpenChange={(open) => !open && setToast(null)} title={toast?.title} type={toast?.type} />
    </>
  );
};

export const DevSuggestionsTabLabelComponent = ({ shouldShowIcon = true }: ArtifactViewTabLabelProps): ReactElement => {
  const component = useMemo((): ReactElement => {
    return (
      <Flex align="center" gap="2">
        {shouldShowIcon && <MessageSquare size={16} />}
        Dev Suggestions
      </Flex>
    );
  }, [shouldShowIcon]);

  return component;
};
