import { ChevronDownIcon, ChevronRightIcon } from "@radix-ui/react-icons";
import { Badge, Button, Flex, IconButton, Text } from "@radix-ui/themes";
import { useSetAtom } from "jotai";
import { Maximize2Icon } from "lucide-react";
import type { ReactElement } from "react";
import { useEffect, useMemo, useState } from "react";

import { ElementIds } from "../api";
import { isReviewModalOpenAtom } from "../common/state/atoms/modals.ts";
import { CommitChanges } from "../pages/chat/components/CommitChanges.tsx";
import { getChangeStatsFromDiff, parseDiff } from "./DiffUtils";
import styles from "./DiffView.module.scss";
import { INITIAL_VISIBLE_FILE_COUNT, SingleFileDiff } from "./SingleFileDiff";
import { TooltipIconButton } from "./TooltipIconButton.tsx";

type DiffViewProps = {
  diff: string | undefined;
};

export const UncommittedDiffView = ({ diff }: DiffViewProps): ReactElement => {
  const [isExpanded, setIsExpanded] = useState(true);
  const setIsReviewModalOpen = useSetAtom(isReviewModalOpenAtom);

  const changeStats = useMemo(() => {
    if (!diff) {
      return { filesChanged: 0, added: 0, removed: 0 };
    }
    return getChangeStatsFromDiff(diff);
  }, [diff]);

  return (
    <Flex direction="column" gapY="3" data-testid={ElementIds.ARTIFACT_UNCOMMITTED_SECTION} diff-string={diff}>
      <Flex justify="between" px="14px">
        <Flex gap="2" align="center" className={styles.heading}>
          <IconButton variant="ghost" size="1" onClick={() => setIsExpanded(!isExpanded)}>
            {!isExpanded && <ChevronRightIcon />}
            {isExpanded && <ChevronDownIcon />}
          </IconButton>
          <Text className={styles.headingText}>Uncommitted Changes</Text>
          <Badge>{changeStats.filesChanged}</Badge>
        </Flex>
        <Flex gap="14px" align="center" className={styles.diffStats}>
          <Text className={styles.linesAdded}>+{changeStats.added}</Text>
          <Text className={styles.linesRemoved}>-{changeStats.removed}</Text>
          <TooltipIconButton
            tooltipText="Review changes"
            variant="ghost"
            size="1"
            onClick={() => setIsReviewModalOpen(true)}
          >
            <Maximize2Icon />
          </TooltipIconButton>
        </Flex>
      </Flex>
      <Flex direction="column" gap="2">
        {isExpanded && diff && !!diff.trim() && <CommitChanges />}
        {isExpanded && <DiffView diff={diff} />}
      </Flex>
    </Flex>
  );
};

export const CommittedDiffView = ({ diff }: DiffViewProps): ReactElement => {
  const [isExpanded, setIsExpanded] = useState(false);

  const changeStats = useMemo(() => {
    if (!diff) {
      return { filesChanged: 0, added: 0, removed: 0 };
    }
    return getChangeStatsFromDiff(diff);
  }, [diff]);

  return (
    <Flex direction="column" gapY="3" data-testid={ElementIds.ARTIFACT_COMMITTED_SECTION} diff-string={diff}>
      <Flex justify="between" px="14px">
        <Flex gap="2" align="center" className={styles.heading}>
          <IconButton
            variant="ghost"
            size="1"
            onClick={() => setIsExpanded(!isExpanded)}
            data-testid={ElementIds.ARTIFACT_COMMITTED_SECTION_EXPAND}
            data-state={isExpanded ? "expanded" : "collapsed"}
          >
            {!isExpanded && <ChevronRightIcon />}
            {isExpanded && <ChevronDownIcon />}
          </IconButton>
          <Text className={styles.headingText}>Committed Changes</Text>
          <Badge>{changeStats.filesChanged}</Badge>
        </Flex>
        <Flex gap="14px" align="center" className={styles.diffStats}>
          <Text className={styles.linesAdded}>+{changeStats.added}</Text>
          <Text className={styles.linesRemoved}>-{changeStats.removed}</Text>
        </Flex>
      </Flex>
      {isExpanded && <DiffView diff={diff} />}
    </Flex>
  );
};

export const DiffView = ({ diff }: DiffViewProps): ReactElement => {
  const [visibleFileCount, setVisibleFileCount] = useState(INITIAL_VISIBLE_FILE_COUNT);

  const { diffStrings } = useMemo(() => {
    if (diff === undefined) {
      return { diffStrings: [] };
    }
    return parseDiff(diff);
  }, [diff]);

  const visibleDiffStrings = useMemo(() => {
    return diffStrings.slice(0, visibleFileCount);
  }, [diffStrings, visibleFileCount]);

  const [visibleStates, setVisibleStates] = useState(() => visibleDiffStrings.map(() => false));

  useEffect(() => {
    setVisibleStates((prev) => {
      const newLength = visibleDiffStrings.length;
      const currentLength = prev.length;

      if (newLength > currentLength) {
        return [...prev, ...Array(newLength - currentLength).fill(false)];
      }

      return prev;
    });
  }, [visibleDiffStrings.length]);

  if (!diff || diff.trim() === "") {
    return (
      <Flex className={styles.noChanges} justify="center" align="center" p="3">
        <Text color="gray">No changes yet</Text>
      </Flex>
    );
  }

  const onToggleVisibility = (index: number): void => {
    setVisibleStates((prev) => {
      console.log("Toggling visibility for index:", index, "Current state:", prev);
      const newStates = [...prev];
      newStates[index] = !newStates[index];
      return newStates;
    });
  };

  const handleLoadMoreFiles = (): void => {
    const nextBatchSize = Math.min(INITIAL_VISIBLE_FILE_COUNT, diffStrings.length - visibleFileCount);
    setVisibleFileCount((prev) => prev + nextBatchSize);
  };

  return (
    <Flex direction="column" gapY="3">
      {visibleDiffStrings.map((diff, index) => (
        <SingleFileDiff
          key={index}
          diffString={diff}
          isBodyVisible={visibleStates[index]}
          onToggleBodyVisible={() => onToggleVisibility(index)}
        />
      ))}
      {/* Show load more button if there are more files */}
      {diffStrings.length > visibleFileCount && (
        <Flex justify="center" align="center" p="3">
          <Flex direction="column" align="center" gapY="2">
            <Text color="gray" size="2">
              Showing {visibleFileCount} of {diffStrings.length} files
            </Text>
            <Button onClick={handleLoadMoreFiles} variant="soft" size="2">
              Load {Math.min(INITIAL_VISIBLE_FILE_COUNT, diffStrings.length - visibleFileCount)} more files (
              {diffStrings.length - visibleFileCount} remaining)
            </Button>
          </Flex>
        </Flex>
      )}
    </Flex>
  );
};
