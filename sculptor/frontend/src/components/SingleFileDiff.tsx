import { ChevronDownIcon, ChevronRightIcon, MinusIcon, PlusIcon } from "@radix-ui/react-icons";
import { Box, Flex, Text, Tooltip } from "@radix-ui/themes";
import hljs from "highlight.js/lib/core";
import css from "highlight.js/lib/languages/css";
import python from "highlight.js/lib/languages/python";
import tsx from "highlight.js/lib/languages/typescript";
import { ArrowDownFromLineIcon, ArrowUpFromLineIcon, CopyIcon, SeparatorHorizontalIcon } from "lucide-react";
import type { LegacyRef, ReactElement } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ElementIds, getFile } from "../api";
import { useHoverWithRef } from "../common/Hooks.ts";
import { useImbueParams } from "../common/NavigateUtils.ts";
import { mergeClasses, useResolvedTheme } from "../common/Utils.ts";
import type { DiffFileNames, HunkData } from "./DiffUtils.ts";
import { extractFileNamesFromDiff, getLineCounts, parseDiffIntoHunks } from "./DiffUtils.ts";
import styles from "./SingleFileDiff.module.scss";
import { Toast, type ToastContent, ToastType } from "./Toast.tsx";
import { TooltipIconButton } from "./TooltipIconButton.tsx";

hljs.registerLanguage("python", python);
hljs.registerLanguage("typescript", tsx);
hljs.registerLanguage("css", css);

// Hook to dynamically load the right highlight.js theme
const useHighlightTheme = (theme: "light" | "dark"): void => {
  useEffect(() => {
    // Remove existing hljs theme
    const existing = document.getElementById("hljs-theme");
    if (existing) {
      existing.remove();
    }

    // Add new theme
    const link = document.createElement("link");
    link.id = "hljs-theme";
    link.rel = "stylesheet";

    // Set the href to the actual CSS file
    const themeFile = theme === "dark" ? "github-dark.css" : "github-light.css";
    link.href = `https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.11.1/styles/${themeFile}`;

    document.head.appendChild(link);
  }, [theme]);
};

export const DIFF_MAX_LINE_LENGTH = 125;
export const INITIAL_VISIBLE_FILE_COUNT = 20;
export const DIFF_INCREMENTAL_LINES = 50;

export const SingleFileDiffHeader = (props: {
  isBodyVisible: boolean;
  onToggleBodyVisible: () => void;
  linesAdded: number;
  linesRemoved: number;
  fileNames: DiffFileNames;
}): ReactElement => {
  const [shouldShowCopyToast, setShouldShowCopyToast] = useState(false);

  const { hoverRef, isHovered } = useHoverWithRef<HTMLDivElement>();

  let baseFileName = props.fileNames.newFileName;
  let fileNameAnnotation: string | null = null;

  if (props.fileNames.previousFileName === null) {
    fileNameAnnotation = " (new)";
  } else if (props.fileNames.newFileName === null) {
    baseFileName = props.fileNames.previousFileName;
    fileNameAnnotation = " (deleted)";
  } else if (props.fileNames.previousFileName !== props.fileNames.newFileName) {
    baseFileName = props.fileNames.previousFileName + " → " + props.fileNames.newFileName;
  }
  const annotatedFileName = baseFileName + (fileNameAnnotation ? fileNameAnnotation : "");

  const handleCopyFilename = (): void => {
    if (navigator.clipboard) {
      navigator.clipboard?.writeText(props.fileNames.referenceFileName);
      setShouldShowCopyToast(true);
    } else {
      console.log("No clipboard access");
    }
  };

  return (
    <>
      <Flex
        direction="row"
        align="center"
        justify="between"
        px="3"
        py="1"
        className={styles.singleFileDiffHeader}
        style={{ cursor: "pointer" }}
        data-filename={props.fileNames.referenceFileName}
        data-testid={ElementIds.ARTIFACT_FILE_HEADER}
        onClick={props.onToggleBodyVisible}
      >
        <Flex
          direction="row"
          align="center"
          gap="3"
          minWidth="0"
          pr="3"
          // TODO: this is hacky but I'm not sure why our type system keeps complaining about refs
          ref={hoverRef as LegacyRef<HTMLDivElement> | undefined}
        >
          <Box onClick={(e) => e.stopPropagation()} className={styles.chevronBox}>
            {/* TODO (claude): add a data-testid to this so we can access this from the POM and use this to click to toggle visibility */}
            <TooltipIconButton
              tooltipText={props.isBodyVisible ? "Hide" : "Show"}
              onClick={props.onToggleBodyVisible}
              data-testid={ElementIds.ARTIFACT_FILE_DROPDOWN}
            >
              {props.isBodyVisible ? <ChevronDownIcon /> : <ChevronRightIcon />}
            </TooltipIconButton>
          </Box>
          <Tooltip content={annotatedFileName} delayDuration={700}>
            <Flex direction="row" gap="2" minWidth="0">
              <Text truncate={true} data-testid={ElementIds.ARTIFACT_FILE_NAME}>
                {baseFileName}
              </Text>
              {fileNameAnnotation && <Text className={styles.filenameAnnotation}>{fileNameAnnotation}</Text>}
            </Flex>
          </Tooltip>
          {isHovered && (
            <TooltipIconButton
              tooltipText="Copy filename"
              onClick={(e: React.MouseEvent<HTMLButtonElement>) => {
                e.stopPropagation();
                handleCopyFilename();
              }}
              size="1"
            >
              <CopyIcon />
            </TooltipIconButton>
          )}
        </Flex>
        <Flex direction="row" align="center" gap="3">
          <Text className={styles.darkGreen}>+{props.linesAdded}</Text>
          <Text className={styles.darkRed}>-{props.linesRemoved}</Text>
        </Flex>
      </Flex>
      <Toast open={shouldShowCopyToast} onOpenChange={setShouldShowCopyToast} title="Filename copied to clipboard" />
    </>
  );
};

const getGutterClass = (mode: "added" | "removed" | "context"): string => {
  const baseClass = styles.diffLineGutter;
  if (mode === "added") return `${baseClass} ${styles.diffLineGutterAdded}`;
  if (mode === "removed") return `${baseClass} ${styles.diffLineGutterRemoved}`;
  return baseClass;
};

const getLineClass = (mode: "added" | "removed" | "context"): string => {
  if (mode === "added") return styles.diffLineAdded;
  if (mode === "removed") return styles.diffLineRemoved;
  return styles.diffLineContext;
};

const DiffLine = (props: {
  content: string;
  mode: "added" | "removed" | "context";
  oldLineNumber: number | null;
  newLineNumber: number | null;
}): ReactElement => {
  const codeContent = props.content.slice(1); // remove leading +/-
  const highlightedCode = hljs.highlightAuto(codeContent).value;

  const gutterClass = getGutterClass(props.mode);
  const lineClass = getLineClass(props.mode);

  let icon: ReactElement | null = null;

  if (props.mode === "added") icon = <PlusIcon className={mergeClasses(styles.darkGreen, styles.diffIcon)} />;
  if (props.mode === "removed") icon = <MinusIcon className={mergeClasses(styles.darkRed, styles.diffIcon)} />;

  return (
    <Flex className={lineClass}>
      <Flex direction="row" className={gutterClass} gapX="3" width="6em" justify="between" pl="2" pr="2">
        <Box width="3" className={props.mode === "removed" ? styles.darkRed : ""}>
          {props.oldLineNumber ?? ""}
        </Box>
        <Text className={props.mode === "added" ? styles.darkGreen : ""}>{props.newLineNumber ?? ""}</Text>
      </Flex>
      <Flex gapX="5" width="100%" pl="3" align="start">
        {icon ? icon : <Box style={{ minWidth: "12px" }} />}
        <Box className={styles.diffLineContent} dangerouslySetInnerHTML={{ __html: highlightedCode }} />
      </Flex>
    </Flex>
  );
};

const TruncatedDiffGap = (props: {
  remainingLines: number;
  setVisibleLines: (updater: (prev: number) => number) => void;
}): ReactElement => {
  const linesToShow = Math.min(DIFF_INCREMENTAL_LINES, props.remainingLines);

  if (props.remainingLines === 0) {
    return <></>;
  }

  return (
    <Flex direction="row" align="center" className={styles.gapHeader}>
      <TooltipIconButton
        tooltipText="Show more lines"
        className={styles.expandButton}
        onClick={() => props.setVisibleLines((prev) => prev + DIFF_INCREMENTAL_LINES)}
      >
        <SeparatorHorizontalIcon size="16px" />
      </TooltipIconButton>
      <Text size="2" color="gray" ml="2">
        Show {linesToShow} more lines ({props.remainingLines} remaining)
      </Text>
    </Flex>
  );
};

const Gap = (props: {
  filePath: string;
  startLine: number;
  endLine: number;
  fileContents: string | null;
  setFileContents: (contents: string | null) => void;
  newStartLine: number;
  kind: "UP" | "DOWN" | "BETWEEN";
}): ReactElement => {
  const [isExpanded, setIsExpanded] = useState(false);
  const [toast, setToast] = useState<ToastContent | null>(null);
  const { projectID, taskID } = useImbueParams();

  if (!projectID) {
    throw new Error("Expected projectID to be defined");
  }

  if (!taskID) {
    throw new Error("Expected taskID to be defined");
  }

  const contextLines = useMemo(() => {
    if (!props.fileContents || !isExpanded) {
      return [];
    }
    const lines = props.fileContents.split("\n");
    const start = props.newStartLine - 1; // Convert to 0-based index
    const end = props.endLine + (props.newStartLine - props.startLine); // This is exclusive, so it works as is
    return lines.slice(start, end);
  }, [props.fileContents, isExpanded, props.startLine, props.endLine, props.newStartLine]);

  const handleExpand = async (): Promise<void> => {
    if (isExpanded) {
      setIsExpanded(false);
      return;
    }

    try {
      const { data: contents } = await getFile({
        path: { project_id: projectID, task_id: taskID },
        body: { filePath: props.filePath },
      });

      if (contents) {
        props.setFileContents(contents);
        setIsExpanded(true);
      }
    } catch (error) {
      console.error("Failed to fetch file contents:", error);
      setToast({ title: "Failed to fetch file contents", type: ToastType.ERROR });
    }
  };

  let icon: ReactElement;
  if (props.kind === "UP") icon = <ArrowUpFromLineIcon size="16px" />;
  else if (props.kind === "DOWN") icon = <ArrowDownFromLineIcon size="16px" />;
  else icon = <SeparatorHorizontalIcon size="16px" />;

  if (!isExpanded) {
    return (
      <>
        <Flex direction="row" align="center" className={styles.gapHeader}>
          {/*FIXME: do we still need these styles here? */}
          <TooltipIconButton tooltipText="Expand" className={styles.expandButton} onClick={handleExpand}>
            {icon}
          </TooltipIconButton>
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

  let oldLineNum = props.startLine;
  let newLineNum = props.newStartLine;
  return (
    <>
      <Flex direction="column">
        {contextLines.map((line, idx) => {
          const content = " " + line;
          const el = (
            <DiffLine
              key={idx}
              content={content}
              mode="context"
              oldLineNumber={oldLineNum}
              newLineNumber={newLineNum}
            />
          );
          oldLineNum++;
          newLineNum++;
          return el;
        })}
      </Flex>
      <Toast open={!!toast} onOpenChange={(open) => !open && setToast(null)} title={toast?.title} type={toast?.type} />
    </>
  );
};

const Hunk = (props: { hunk: HunkData }): ReactElement => {
  let oldLineCounter = props.hunk.meta.oldStart;
  let newLineCounter = props.hunk.meta.newStart;

  return (
    <Flex direction="column" width="100%">
      {props.hunk.lines.map((rawLine, i) => {
        if (rawLine === "\\ No newline at end of file") return null;

        const ch = rawLine[0] || " ";
        let mode: "added" | "removed" | "context" = "context";
        if (ch === "+") mode = "added";
        if (ch === "-") mode = "removed";

        let oldNum: number | null = null;
        let newNum: number | null = null;
        if (mode === "removed") {
          oldNum = oldLineCounter++;
        } else if (mode === "added") {
          newNum = newLineCounter++;
        } else {
          oldNum = oldLineCounter++;
          newNum = newLineCounter++;
        }

        return <DiffLine key={i} content={rawLine} mode={mode} oldLineNumber={oldNum} newLineNumber={newNum} />;
      })}
    </Flex>
  );
};

export const SingleFileDiff = (props: {
  diffString: string;
  isBodyVisible: boolean;
  onToggleBodyVisible: () => void;
}): ReactElement => {
  const [visibleLineCount, setVisibleLineCount] = useState(DIFF_MAX_LINE_LENGTH);
  const [fileContents, setFileContents] = useState<string | null>(null);

  // Use the resolved theme and apply it to highlight.js
  const resolvedTheme = useResolvedTheme();
  useHighlightTheme(resolvedTheme);

  const { added, removed } = useMemo(() => getLineCounts(props.diffString), [props.diffString]);
  const fileNames = useMemo(() => extractFileNamesFromDiff(props.diffString), [props.diffString]);

  // Parse hunks once, then process them lazily
  const allHunks = useMemo(() => parseDiffIntoHunks(props.diffString), [props.diffString]);

  const processHunksLazily = useCallback(
    (lineLimit: number): { visibleHunks: Array<HunkData>; remainingLines: number } => {
      let lineCount = 0;
      const visibleHunks: Array<HunkData> = [];
      let remainingLines = 0;

      for (let i = 0; i < allHunks.length; i++) {
        const hunk = allHunks[i];

        if (lineCount + hunk.lines.length <= lineLimit) {
          // Whole hunk fits
          visibleHunks.push(hunk);
          lineCount += hunk.lines.length;
        } else if (lineCount < lineLimit) {
          // Need to split this hunk
          const linesToTake = lineLimit - lineCount;
          const visiblePart = { ...hunk, lines: hunk.lines.slice(0, linesToTake) };
          visibleHunks.push(visiblePart);

          // Calculate remaining lines in this hunk and all subsequent hunks
          remainingLines = hunk.lines.length - linesToTake;
          for (let j = i + 1; j < allHunks.length; j++) {
            remainingLines += allHunks[j].lines.length;
          }
          break;
        } else {
          // Calculate remaining lines in all subsequent hunks
          for (let j = i; j < allHunks.length; j++) {
            remainingLines += allHunks[j].lines.length;
          }
          break;
        }
      }

      return { visibleHunks, remainingLines };
    },
    [allHunks], // only re-create the function if allHunks changes
  );

  const { visibleHunks, remainingLines } = useMemo(
    () => processHunksLazily(visibleLineCount),
    [visibleLineCount, processHunksLazily],
  );

  // TODO: fetch this from the backend
  const totalFileLines = 500;
  let oldCounter = 1;
  let newCounter = 1;
  const elements: Array<ReactElement> = [];

  visibleHunks.forEach((hunk, i) => {
    const { oldStart, oldLines, newStart, newLines } = hunk.meta;
    if (oldStart > oldCounter || newStart > newCounter) {
      const kind = i === 0 ? "UP" : "BETWEEN";
      elements.push(
        <Gap
          key={`gap-${i}`}
          filePath={fileNames.referenceFileName}
          startLine={oldCounter}
          endLine={oldStart - 1}
          newStartLine={newCounter}
          kind={kind}
          fileContents={fileContents}
          setFileContents={setFileContents}
        />,
      );
    }
    elements.push(<Hunk key={`hunk-${i}`} hunk={hunk} />);
    oldCounter = oldStart + oldLines;
    newCounter = newStart + newLines;
  });

  // Use TruncatedDiffGap for remaining lines
  if (remainingLines > 0) {
    elements.push(
      <TruncatedDiffGap key="truncated-gap" remainingLines={remainingLines} setVisibleLines={setVisibleLineCount} />,
    );
  }

  if (oldCounter <= totalFileLines) {
    elements.push(
      <Gap
        key="gap-final"
        filePath={fileNames.referenceFileName}
        startLine={oldCounter}
        endLine={totalFileLines}
        newStartLine={newCounter} // final gap new start
        kind="DOWN"
        fileContents={fileContents}
        setFileContents={setFileContents}
      />,
    );
  }

  return (
    <Flex width="100%" direction="column" data-testid={ElementIds.ARTIFACT_FILE}>
      <SingleFileDiffHeader
        isBodyVisible={props.isBodyVisible}
        onToggleBodyVisible={props.onToggleBodyVisible}
        linesAdded={added}
        linesRemoved={removed}
        fileNames={fileNames}
      />
      {props.isBodyVisible && (
        <Flex
          flexGrow="1"
          direction="column"
          width="100%"
          className={styles.diffContent}
          data-testid={ElementIds.ARTIFACT_FILE_BODY}
        >
          {elements}
        </Flex>
      )}
    </Flex>
  );
};
