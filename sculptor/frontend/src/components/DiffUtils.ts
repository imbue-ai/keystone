export const getLineCounts = (diffStr: string): { added: number; removed: number } => {
  let added = 0;
  let removed = 0;
  const lines = diffStr.split("\n").slice(5);
  for (const line of lines) {
    if (line.startsWith("@@") || line.startsWith("+++") || line.startsWith("---")) continue;
    if (line.startsWith("+")) added++;
    else if (line.startsWith("-")) removed++;
  }
  return { added, removed };
};

type HunkMeta = {
  oldStart: number;
  oldLines: number;
  newStart: number;
  newLines: number;
  headerLine: string;
};

export const parseHunkHeader = (line: string): HunkMeta | null => {
  const regex = /^@@ -(\d+),?(\d+)? \+(\d+),?(\d+)? @@/;
  const match = line.match(regex);
  if (!match) return null;
  const [, oldStart, oldLen, newStart, newLen] = match;
  return {
    oldStart: parseInt(oldStart, 10),
    oldLines: parseInt(oldLen || "1", 10),
    newStart: parseInt(newStart, 10),
    newLines: parseInt(newLen || "1", 10),
    headerLine: line,
  };
};

export type HunkData = {
  meta: HunkMeta;
  lines: Array<string>;
};

export type DiffFileNames = {
  previousFileName: string | null;
  newFileName: string | null;
  // referenceFileName is usually the newFileName, unless the file got deleted, in which case it's the previousFileName.
  referenceFileName: string;
};

export const parseDiffIntoHunks = (diffString: string): Array<HunkData> => {
  const lines = diffString.split("\n");
  const hunks: Array<HunkData> = [];
  let current: HunkData | null = null;

  for (const line of lines) {
    if (line.startsWith("@@")) {
      const meta = parseHunkHeader(line);
      if (meta) {
        if (current) hunks.push(current);
        current = { meta, lines: [] };
      }
    } else {
      if (current) current.lines.push(line);
    }
  }
  if (current) hunks.push(current);
  return hunks;
};

export const splitDiffByFiles = (multiFileDiffString: string): Array<string> => {
  return multiFileDiffString
    .trim()
    .split(/(?=^diff --git )/m)
    .map((chunk: string) => chunk.trim())
    .filter((chunk: string) => chunk.length > 0);
};

export const extractChangedFiles = (diffStrings: Array<string>): Array<DiffFileNames> => {
  return diffStrings.map((diffString: string) => extractFileNamesFromDiff(diffString));
};

const unescapeDiffFilename = (filename: string): string => {
  // Git diffs escape certain characters using C-style backslash escapes.
  // See https://git-scm.com/docs/git-config#Documentation/git-config.txt-corequotePath
  //
  // Filenames that contain escapes will always be quoted using double quotes.
  // We can use JSON.parse to unescape such file names.
  if (!filename.startsWith('"')) {
    // Not quoted, so no escapes
    return filename;
  } else {
    try {
      const unescaped_filename = JSON.parse(filename);
      if (typeof unescaped_filename === "string") {
        return unescaped_filename;
      } else {
        throw new Error("Parsed filename is not a string");
      }
    } catch (e) {
      console.error("Failed to unescape diff filename:", filename, e);
      return filename;
    }
  }
};

export const extractFileNamesFromDiff = (diffString: string): DiffFileNames => {
  // Split the diff into header and body. Everything up to the first line starting with @@ (if any) is the header.
  const header_match = diffString.match(/^(.*?)(?=^@@)/ms);
  const diffHeader = header_match ? header_match[1] : diffString;

  // Parse out the file names from the header. This is somewhat complicated:
  // 1. If the file is not renamed, we can get the filename from the `diff --git a/filename b/filename` line.
  // 2. However, if the file is renamed, then this header becomes ambiguous. In particular, if the filename contains the sequence ` b/`,
  //   then we cannot reliably parse the filename from this line.
  //   In that case, we instead need to look for the `rename from` and `rename to` lines.
  // 3. We will also check for "deleted file mode" and "new file mode" to determine if the file is deleted or new.
  //
  // See https://git-scm.com/docs/diff-format#generate_patch_text_with_p to understand the various lines in the header.
  const diffGitMatch = diffHeader.match(/^diff --git ("?)a\/(.+?) \1b\/\2$/m); // Only matches if the filenames are identical
  const renameFromMatch = diffHeader.match(/^rename from (.+)$/m);
  const renameToMatch = diffHeader.match(/^rename to (.+)$/m);
  const copyFromMatch = diffHeader.match(/^copy from (.+)$/m);
  const copyToMatch = diffHeader.match(/^copy to (.+)$/m);
  const deletedFileMatch = diffHeader.match(/^deleted file mode/m);
  const newFileMatch = diffHeader.match(/^new file mode/m);
  let previousFileName: string | null = "unknown_file";
  let newFileName: string | null = "unknown_file";
  if (renameFromMatch && renameToMatch) {
    // Renamed file
    previousFileName = unescapeDiffFilename(renameFromMatch[1]);
    newFileName = unescapeDiffFilename(renameToMatch[1]);
  } else if (copyFromMatch && copyToMatch) {
    // Copied file (we currently do not distinguish between copy and rename in the output)
    previousFileName = unescapeDiffFilename(copyFromMatch[1]);
    newFileName = unescapeDiffFilename(copyToMatch[1]);
  } else if (diffGitMatch) {
    // Not a rename - same filename in both previous and new
    const fileName = unescapeDiffFilename(diffGitMatch[1] + diffGitMatch[2]);
    previousFileName = fileName;
    newFileName = fileName;
  } else {
    console.warn("Could not parse filenames from diff header:", diffHeader);
  }

  if (deletedFileMatch) {
    newFileName = null;
  }

  if (newFileMatch) {
    previousFileName = null;
  }
  const referenceFileName = newFileName || previousFileName || "unknown_file";

  return { previousFileName, newFileName, referenceFileName };
};

export const calculateTotalLineChanges = (diffStrings: Array<string>): { added: number; removed: number } => {
  const lineCounts = diffStrings.map(getLineCounts);
  const totalAdded = lineCounts.reduce((acc: number, lc: { added: number; removed: number }) => acc + lc.added, 0);
  const totalRemoved = lineCounts.reduce((acc: number, lc: { added: number; removed: number }) => acc + lc.removed, 0);

  return {
    added: totalAdded,
    removed: totalRemoved,
  };
};

export type ChangeStatsType = {
  filesChanged: number;
  added: number;
  removed: number;
};

export type DiffData = {
  diffStrings: Array<string>;
  changedFiles: Array<DiffFileNames>;
  changeStats: ChangeStatsType;
  fileChanges: Array<{
    fileNames: DiffFileNames;
    diffString: string;
    changes: { added: number; removed: number };
  }>;
};

export const parseDiff = (multiFileDiffString: string): DiffData => {
  const diffStrings = splitDiffByFiles(multiFileDiffString);
  const changedFiles = extractChangedFiles(diffStrings);
  const { added, removed } = calculateTotalLineChanges(diffStrings);

  const fileChanges = diffStrings.map((diffString, index) => {
    const fileNames = changedFiles[index];
    const changes = getLineCounts(diffString);

    return {
      fileNames,
      diffString,
      changes,
    };
  });

  return {
    diffStrings,
    changedFiles,
    changeStats: {
      filesChanged: changedFiles.length,
      added,
      removed,
    },
    fileChanges,
  };
};

export const getChangeStatsFromDiff = (multiFileDiffString: string): ChangeStatsType => {
  const diffData = parseDiff(multiFileDiffString);
  return {
    filesChanged: diffData.changedFiles.length,
    added: diffData.changeStats.added,
    removed: diffData.changeStats.removed,
  };
};
