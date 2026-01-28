import { ChevronDownIcon, ChevronLeftIcon, ChevronRightIcon, ChevronUpIcon } from "@radix-ui/react-icons";
import { Badge, Box, Button, Dialog, DropdownMenu, Flex, Spinner, Text } from "@radix-ui/themes";
import { ShieldCheck } from "lucide-react";
import { type ReactElement, useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { CostMessage, EvidenceMessage, ScoreMessage, ScoutEvidenceExample } from "../../../../api/index.ts";
import { ArtifactType } from "../../../../api/index.ts";
import { Toast, type ToastContent } from "../../../../components/Toast.tsx";
import type {
  ArtifactViewContentProps,
  ArtifactViewTabLabelProps,
  CheckHistory,
  ScoutOutputWithSource,
} from "../../Types.ts";
import { CheckStatusDisplay, getCheckStatusDisplay } from "../../utils/checkStatusUtils.ts";
import styles from "./ScoutArtifactView.module.scss";
import { filterScoutOutputsFromCheckOutputs, getScoutOutputForMessage } from "./scoutUtils.ts";

// Helper functions to extract metadata from scout outputs
type ScoutRunMetadata = {
  timeElapsed?: number;
  totalCost?: number;
  evidenceCount: number;
  overallScore?: number;
};

const getScoutRunMetadata = (scoutOutputs: Array<ScoutOutputWithSource>): ScoutRunMetadata => {
  let timeElapsed: number | undefined;
  let totalCost: number | undefined;
  let evidenceCount = 0;
  let overallScore: number | undefined;

  for (const output of scoutOutputs) {
    const data = output.output.data;

    if (data.objectType === "ScoreMessage") {
      const scoreMessage = data as ScoreMessage;
      timeElapsed = scoreMessage.timeElapsed;
      evidenceCount = scoreMessage.evidenceCount;
      overallScore = scoreMessage.overallScore;
    } else if (data.objectType === "CostMessage") {
      const costMessage = data as CostMessage;
      totalCost = costMessage.totalCostUsd;
    }
  }

  return { timeElapsed, totalCost, evidenceCount, overallScore };
};

const getScoreColor = (score: number): string => {
  if (score < 0.4) {
    return "var(--red-9)";
  } else if (score < 0.95) {
    return "var(--orange-9)";
  } else {
    return "var(--green-9)";
  }
};

const isScoutRunning = (
  checksData: Record<string, Record<string, CheckHistory>> | undefined,
  messageId: string | undefined,
): boolean => {
  if (!checksData || !messageId) return false;

  const scoutCheck = checksData[messageId]?.["Scout"];
  if (!scoutCheck) return false;

  const latestRunId = scoutCheck.runIds?.[scoutCheck.runIds.length - 1];
  const latestStatus = latestRunId ? scoutCheck.statusByRunId[latestRunId] : null;
  const checkStatus = getCheckStatusDisplay(latestStatus);

  return checkStatus === CheckStatusDisplay.RUNNING;
};

const getScoreBadgeColor = (score: "Good" | "Moderate" | "Bad"): "green" | "orange" | "red" => {
  switch (score) {
    case "Good":
      return "green";
    case "Moderate":
      return "orange";
    case "Bad":
      return "red";
  }
};

const getConfidenceBadgeColor = (confidence: "High" | "Medium" | "Low"): "blue" | "yellow" | "gray" => {
  switch (confidence) {
    case "High":
      return "blue";
    case "Medium":
      return "yellow";
    case "Low":
      return "gray";
  }
};

// Component for displaying a single evidence example
const EvidenceExampleComponent = ({ example }: { example: ScoutEvidenceExample }): ReactElement => {
  const [isImageModalOpen, setIsImageModalOpen] = useState(false);

  return (
    <Box className={styles.exampleItem}>
      <Flex className={styles.exampleHeader}>
        <Text className={styles.exampleDescription}>{example.description}</Text>
        <Badge color={example.type === "positive" ? "green" : "red"} size="1">
          {example.type}
        </Badge>
      </Flex>

      <Box className={styles.exampleContent}>
        {example.command && (
          <Box mb="2">
            <Text size="1" color="gray" weight="medium" as="div" mb="1">
              Command:
            </Text>
            <Box className={styles.codeBlock}>
              <code>{example.command}</code>
            </Box>
          </Box>
        )}

        {example.output && (
          <Box mb="2">
            <Text size="1" color="gray" weight="medium" as="div" mb="1">
              Output:
            </Text>
            <Box className={styles.commandOutput}>{example.output}</Box>
          </Box>
        )}

        {example.code && (
          <Box mb="2">
            <Text size="1" color="gray" weight="medium" as="div" mb="1">
              Code:
            </Text>
            <Box className={styles.codeBlock}>
              <pre style={{ margin: 0 }}>{example.code}</pre>
            </Box>
          </Box>
        )}

        {example.imageData && example.imageFormat && (
          <Box>
            <Text size="1" color="gray" weight="medium" as="div" mb="1">
              {example.imageCaption || "Image"}:
            </Text>
            <Dialog.Root open={isImageModalOpen} onOpenChange={setIsImageModalOpen}>
              <Dialog.Trigger>
                <img
                  src={`data:image/${example.imageFormat};base64,${example.imageData}`}
                  alt={example.imageCaption || "Evidence image"}
                  className={styles.imagePreview}
                />
              </Dialog.Trigger>
              <Dialog.Content className={styles.dialogContent}>
                <Dialog.Title>{example.imageCaption || "Evidence Image"}</Dialog.Title>
                <Box className={styles.dialogImageContainer}>
                  <img
                    src={`data:image/${example.imageFormat};base64,${example.imageData}`}
                    alt={example.imageCaption || "Evidence image"}
                    className={styles.dialogImage}
                  />
                </Box>
              </Dialog.Content>
            </Dialog.Root>
          </Box>
        )}
      </Box>
    </Box>
  );
};

// Helper to format reference text with code blocks
const formatReferenceText = (text: string): ReactElement => {
  // Match code blocks in markdown format: ```language\ncode\n```
  const codeBlockRegex = /```(\w+)?\n([\s\S]*?)```/g;
  const parts: Array<{ type: "text" | "code"; content: string; language?: string }> = [];
  let lastIndex = 0;
  let match;

  while ((match = codeBlockRegex.exec(text)) !== null) {
    // Add text before code block
    if (match.index > lastIndex) {
      parts.push({ type: "text", content: text.substring(lastIndex, match.index) });
    }
    // Add code block
    parts.push({ type: "code", content: match[2], language: match[1] });
    lastIndex = match.index + match[0].length;
  }

  // Add remaining text
  if (lastIndex < text.length) {
    parts.push({ type: "text", content: text.substring(lastIndex) });
  }

  // If no code blocks found, return as plain text
  if (parts.length === 0) {
    return (
      <Text className={`${styles.evidenceResult} ${styles.evidenceResultItalic}`} size="2">
        {text}
      </Text>
    );
  }

  return (
    <Box>
      {parts.map((part, idx) =>
        part.type === "code" ? (
          <Box key={idx} className={styles.codeBlock} mb="2">
            <pre style={{ margin: 0 }}>{part.content}</pre>
          </Box>
        ) : (
          <Text key={idx} className={`${styles.evidenceResult} ${styles.evidenceResultItalic}`} size="2">
            {part.content}
          </Text>
        ),
      )}
    </Box>
  );
};

// Component for displaying a single evidence card
const EvidenceCard = ({ evidence }: { evidence: EvidenceMessage }): ReactElement => {
  const [isExpanded, setIsExpanded] = useState(false); // Main card collapsed by default
  const [isExamplesExpanded, setIsExamplesExpanded] = useState(false);
  const [isReferenceExpanded, setIsReferenceExpanded] = useState(false);

  return (
    <Box className={styles.evidenceCard}>
      {/* Always visible header - clickable to expand/collapse */}
      <Flex className={styles.evidenceHeader} onClick={() => setIsExpanded(!isExpanded)}>
        <Flex align="center" gap="2" style={{ flex: 1 }}>
          {isExpanded ? <ChevronUpIcon /> : <ChevronDownIcon />}
          <Text className={styles.evidenceQuestion} size="3" weight="medium">
            {evidence.question}
          </Text>
        </Flex>
        <Flex className={styles.evidenceBadges}>
          <Badge color={getScoreBadgeColor(evidence.score)} size="2">
            {evidence.score}
          </Badge>
          <Badge color={getConfidenceBadgeColor(evidence.confidence)} size="2">
            {evidence.confidence}
          </Badge>
        </Flex>
      </Flex>

      {/* Expandable content */}
      {isExpanded && (
        <Box className={`${styles.evidenceBody} ${styles.evidenceBodyExpanded}`}>
          <Box>
            <Box className={`${styles.expandButton} ${styles.sectionHeader}`}>
              <Text size="2" weight="medium">
                Action
              </Text>
            </Box>
            <Box ml="3">
              <Text className={styles.evidenceResult} size="2">
                {evidence.action}
              </Text>
            </Box>
          </Box>

          <Box>
            <Box className={`${styles.expandButton} ${styles.sectionHeader}`}>
              <Text size="2" weight="medium">
                Result
              </Text>
            </Box>
            <Box ml="3">
              <Text className={styles.evidenceResult} size="2">
                {evidence.result}
              </Text>
            </Box>
          </Box>

          {/* Collapsible Reference Section */}
          {evidence.reference && (
            <Box>
              <Flex
                align="center"
                gap="2"
                className={`${styles.expandButton} ${styles.sectionHeader}`}
                onClick={(e) => {
                  e.stopPropagation();
                  setIsReferenceExpanded(!isReferenceExpanded);
                }}
              >
                {isReferenceExpanded ? <ChevronUpIcon /> : <ChevronDownIcon />}
                <Text size="2" weight="medium">
                  Reference
                </Text>
              </Flex>
              {isReferenceExpanded && <Box ml="3">{formatReferenceText(evidence.reference)}</Box>}
            </Box>
          )}

          {/* Collapsible Examples Section */}
          {evidence.examples && evidence.examples.length > 0 && (
            <Box>
              <Flex
                align="center"
                gap="2"
                className={styles.expandButton}
                onClick={(e) => {
                  e.stopPropagation();
                  setIsExamplesExpanded(!isExamplesExpanded);
                }}
              >
                {isExamplesExpanded ? <ChevronUpIcon /> : <ChevronDownIcon />}
                <Text size="2" weight="medium">
                  Examples ({evidence.examples.length})
                </Text>
              </Flex>

              {isExamplesExpanded && (
                <Box className={styles.evidenceExamples}>
                  {evidence.examples.map((example, idx) => (
                    <EvidenceExampleComponent key={idx} example={example} />
                  ))}
                </Box>
              )}
            </Box>
          )}
        </Box>
      )}
    </Box>
  );
};

// ScoutArtifactView: production view for scout results with detailed evidence display
export const ScoutArtifactViewComponent = ({
  artifacts,
  userMessageIds,
  checksData,
}: ArtifactViewContentProps): ReactElement => {
  const [toast, setToast] = useState<ToastContent | null>(null);
  const [currentMessageIdIndex, setCurrentMessageIdIndex] = useState(0);
  const hasInitialized = useRef(false);
  const previousMessageIdsLength = useRef(0);

  const availableMessageIds = useMemo(() => userMessageIds || [], [userMessageIds]);

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

  const metadata = getScoutRunMetadata(currentTurnScoutOutputs);
  const isRunning = isScoutRunning(checksData, currentMessageId);

  // Extract evidence items from scout outputs
  const evidenceItems = useMemo(() => {
    const items: Array<EvidenceMessage> = [];
    console.log("Extracting evidence from outputs:", currentTurnScoutOutputs.length);
    for (const output of currentTurnScoutOutputs) {
      console.log("Checking output type:", output.output.data.objectType);
      if (output.output.data.objectType === "EvidenceMessage") {
        const data = output.output.data as EvidenceMessage;
        console.log("Found evidence:", data);
        items.push({
          question: data.question,
          action: data.action,
          result: data.result,
          score: data.score,
          confidence: data.confidence,
          reference: data.reference ?? undefined,
          examples: data.examples ?? undefined,
        });
      }
    }
    console.log("Total evidence items extracted:", items.length);
    return items;
  }, [currentTurnScoutOutputs]);

  return (
    <>
      <Flex direction="column" gap="4" p="4" className={styles.container}>
        {/* Navigation Header */}
        <Flex justify="between" align="center">
          <Text size="3" weight="bold" as="div">
            Scout Results
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

        {/* Content Area */}
        {isRunning ? (
          <Flex direction="column" gap="4">
            <Flex direction="column" align="center" justify="center" gap="3" py="4">
              <Spinner size="3" />
              <Text size="3" color="gray">
                Running Scout...
              </Text>
            </Flex>
          </Flex>
        ) : currentTurnScoutOutputs.length === 0 ? (
          <Flex direction="column" align="center" justify="center" gap="2" py="8">
            <Text size="3" color="gray">
              No Scout results available
            </Text>
            <Text size="2" color="gray">
              Run Scout from the panel below to analyze your code
            </Text>
          </Flex>
        ) : (
          <Flex direction="column" gap="4">
            {/* Metadata Section */}
            <Flex gap="4" align="center" p="3" className={`${styles.metadataSection} ${styles.metadataContainer}`}>
              {metadata.timeElapsed !== undefined && (
                <Flex direction="column" gap="1">
                  <Text size="1" color="gray" weight="medium">
                    Time Taken
                  </Text>
                  <Text size="2" weight="bold">
                    {metadata.timeElapsed.toFixed(1)}s
                  </Text>
                </Flex>
              )}
              {metadata.totalCost !== undefined && (
                <Flex direction="column" gap="1">
                  <Text size="1" color="gray" weight="medium">
                    Cost
                  </Text>
                  <Text size="2" weight="bold">
                    ${metadata.totalCost.toFixed(4)}
                  </Text>
                </Flex>
              )}
              <Flex direction="column" gap="1">
                <Text size="1" color="gray" weight="medium">
                  Evidence Count
                </Text>
                <Text size="2" weight="bold">
                  {metadata.evidenceCount}
                </Text>
              </Flex>
              {metadata.overallScore !== undefined && (
                <Flex direction="column" gap="1">
                  <Text size="1" color="gray" weight="medium">
                    Score
                  </Text>
                  <Badge
                    className={styles.scoreBadge}
                    style={{
                      backgroundColor: getScoreColor(metadata.overallScore),
                    }}
                  >
                    {Math.round(metadata.overallScore * 100)}%
                  </Badge>
                </Flex>
              )}
            </Flex>

            {/* Evidence Display Section */}
            <Box>
              <Text size="2" weight="medium" mb="3" as="div">
                Evidence Items ({evidenceItems.length})
              </Text>
              {evidenceItems.length === 0 ? (
                <Text size="2" color="gray">
                  No evidence items found
                </Text>
              ) : (
                <Box className={styles.evidenceList}>
                  {evidenceItems.map((evidence, idx) => (
                    <EvidenceCard key={idx} evidence={evidence} />
                  ))}
                </Box>
              )}
            </Box>
          </Flex>
        )}
      </Flex>

      <Toast open={!!toast} onOpenChange={(open) => !open && setToast(null)} title={toast?.title} type={toast?.type} />
    </>
  );
};

export const ScoutTabLabelComponent = ({ shouldShowIcon = true }: ArtifactViewTabLabelProps): ReactElement => {
  const component = useMemo((): ReactElement => {
    return (
      <Flex align="center" gap="2">
        {shouldShowIcon && <ShieldCheck size={16} />}
        Scout
      </Flex>
    );
  }, [shouldShowIcon]);

  return component;
};
