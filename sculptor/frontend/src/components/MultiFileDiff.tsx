import { Box, Button, Flex, ScrollArea, Text } from "@radix-ui/themes";
import { ChevronsDownUpIcon, ChevronsUpDownIcon, PanelLeft } from "lucide-react";
import type React from "react";
import type { ReactElement } from "react";
import { createRef, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ChangeStats } from "./ChangeStats.tsx";
import type { ChangeStatsType } from "./DiffUtils.ts";
import { parseDiff } from "./DiffUtils.ts";
import { FileTree } from "./FileTree.tsx";
import styles from "./MultiFileDiff.module.scss";
import { INITIAL_VISIBLE_FILE_COUNT, SingleFileDiff } from "./SingleFileDiff.tsx";
import { TooltipIconButton } from "./TooltipIconButton.tsx";

const MultiFileDiffHeader = (props: {
  isHidingExpandAndCollapse: boolean;
  onCollapseAll: () => void;
  onExpandAll: () => void;
  changeStats: ChangeStatsType;
  onToggleFileTree: () => void;
}): ReactElement => {
  return (
    <Flex justify="between" align="center" px="2" py="2">
      <Flex gapX="3" align="center">
        <TooltipIconButton tooltipText="Toggle file tree" onClick={props.onToggleFileTree}>
          <PanelLeft />
        </TooltipIconButton>
        <ChangeStats {...props.changeStats} />
      </Flex>
      {props.isHidingExpandAndCollapse && (
        <Flex gapX="3" align="center">
          <Button onClick={props.onExpandAll} variant="surface" size="1">
            <Flex direction="row" align="center" gapX="2">
              <ChevronsDownUpIcon size="16px" />
              Expand all
            </Flex>
          </Button>
          <Button onClick={props.onCollapseAll} variant="surface" size="1">
            <Flex direction="row" align="center" gapX="2">
              <ChevronsUpDownIcon size="16px" />
              Collapse All
            </Flex>
          </Button>
        </Flex>
      )}
    </Flex>
  );
};

export const MultiFileDiffView = (props: {
  multiFileDiffString: string;
  isDefaultFileTreeVisible?: boolean;
  isHidingExpandAndCollapse?: boolean;
}): ReactElement => {
  const [isFileTreeVisible, setIsFileTreeVisible] = useState(!!props.isDefaultFileTreeVisible);
  const [activeFile, setActiveFile] = useState<string | null>(null);
  const [visibleFileCount, setVisibleFileCount] = useState(INITIAL_VISIBLE_FILE_COUNT);

  // TODO: it's better to pass this as props instead of having this parsing logic at the top of the component
  // TODO: we probably want to move some of this logic to the backend
  const diffData = useMemo(() => {
    return parseDiff(props.multiFileDiffString);
  }, [props.multiFileDiffString]);

  // Truncate the diff data to only show the first N files
  const visibleDiffData = useMemo(() => {
    const { diffStrings, changedFiles, changeStats } = diffData;
    return {
      diffStrings: diffStrings.slice(0, visibleFileCount),
      changedFiles: changedFiles.slice(0, visibleFileCount).map((fileNames) => fileNames.referenceFileName),
      changeStats: {
        ...changeStats,
      },
      isTruncated: diffStrings.length > visibleFileCount,
      totalFiles: diffStrings.length,
    };
  }, [diffData, visibleFileCount]);
  const { diffStrings, changedFiles, changeStats, isTruncated, totalFiles } = visibleDiffData;

  const [isDiffVisible, setIsDiffVisible] = useState<Array<boolean>>(new Array(changedFiles.length).fill(true));
  const diffBlockRefs = useRef(changedFiles.map(() => createRef<HTMLDivElement>()));

  // Update the selected file (in the file tree) when the user scrolls to a new file
  useEffect(() => {
    const observers: Array<IntersectionObserver> = [];

    diffBlockRefs.current.forEach((ref: React.RefObject<HTMLDivElement>, index: number) => {
      const observer = new IntersectionObserver(
        ([entry]) => {
          if (entry.isIntersecting) {
            setActiveFile(changedFiles[index]);
          }
        },
        { threshold: 0 },
      );
      if (ref.current) observer.observe(ref.current);
      observers.push(observer);
    });

    return (): void => observers.forEach((observer) => observer.disconnect());
  }, [changedFiles]);

  const handleFileSelect = useCallback(
    (file: string): void => {
      const idx = changedFiles.indexOf(file);
      if (idx > -1) {
        diffBlockRefs.current[idx].current?.scrollIntoView({ behavior: "instant" });
      }
      setActiveFile(file);
    },
    [changedFiles],
  );

  const handleLoadMoreFiles = useCallback(() => {
    const nextBatchSize = Math.min(INITIAL_VISIBLE_FILE_COUNT, totalFiles - visibleFileCount);
    setVisibleFileCount((prev) => prev + nextBatchSize);
  }, [totalFiles, visibleFileCount]);

  return (
    <Flex direction="column" width="100%" height="100%" gapY="3" data-diffstring={props.multiFileDiffString}>
      <MultiFileDiffHeader
        changeStats={changeStats}
        isHidingExpandAndCollapse={!!props.isHidingExpandAndCollapse}
        onCollapseAll={() => setIsDiffVisible(isDiffVisible.map(() => false))}
        onExpandAll={() => setIsDiffVisible(isDiffVisible.map(() => true))}
        onToggleFileTree={() => setIsFileTreeVisible((v) => !v)}
      />

      {/* TODO: auto-collapse file tree if width is too small */}
      <Flex direction="row" flexGrow="1" width="100%" gapX="53px" style={{ overflow: "hidden", minHeight: 0 }}>
        {isFileTreeVisible && (
          <ScrollArea scrollbars="vertical" className={styles.sidebarScrollArea}>
            <FileTree changedFiles={changedFiles} onFileSelect={handleFileSelect} activeFile={activeFile} />
          </ScrollArea>
        )}

        <ScrollArea scrollbars="vertical" className={styles.contentScrollArea}>
          <Box pr="3">
            {diffStrings.length === 0 ? (
              <Flex className={styles.noChanges} justify="center" align="center" p="5">
                <Text color="gray">No changes yet</Text>
              </Flex>
            ) : (
              <Flex direction="column" gapY="3">
                {diffStrings.map((diffString: string, i: number) => (
                  <div key={i} ref={diffBlockRefs.current[i]}>
                    <SingleFileDiff
                      diffString={diffString}
                      isBodyVisible={isDiffVisible[i]}
                      onToggleBodyVisible={() =>
                        setIsDiffVisible((prev) => {
                          const newArr = [...prev];
                          newArr[i] = !newArr[i];
                          return newArr;
                        })
                      }
                    />
                  </div>
                ))}
                {/* Show load more button if there are more files */}
                {isTruncated && (
                  <Flex justify="center" align="center" p="3">
                    <Flex direction="column" align="center" gapY="2">
                      <Text color="gray" size="2">
                        Showing {visibleFileCount} of {totalFiles} files
                      </Text>
                      <Button onClick={handleLoadMoreFiles} variant="soft" size="2">
                        Load {Math.min(INITIAL_VISIBLE_FILE_COUNT, totalFiles - visibleFileCount)} more files (
                        {totalFiles - visibleFileCount} remaining)
                      </Button>
                    </Flex>
                  </Flex>
                )}
              </Flex>
            )}
          </Box>
        </ScrollArea>
      </Flex>
    </Flex>
  );
};
