import { Flex } from "@radix-ui/themes";
import type { ReactElement } from "react";
import { useEffect, useState } from "react";

import { ElementIds } from "~/api";

import { FilePreview } from "./FilePreview.tsx";

type FilePreviewListProps = {
  files: Array<string>;
  onRemoveFile?: (filePath: string) => void;
};

const isPdfFile = (filePath: string): boolean => {
  return filePath.toLowerCase().endsWith(".pdf");
};

const getFileName = (filePath: string): string => {
  const parts = filePath.split(/[/\\]/);
  return parts[parts.length - 1] || filePath;
};

export const FilePreviewList = ({ files, onRemoveFile }: FilePreviewListProps): ReactElement | undefined => {
  const [filesUrls, setFilesUrls] = useState<Record<string, string>>({});
  const [failedFiles, setFailedFiles] = useState<Set<string>>(new Set());

  // Load files from file system
  useEffect(() => {
    const loadFiles = async (): Promise<void> => {
      if (files.length === 0 || !window.sculptor?.getFileData) return;

      const failed = new Set<string>();

      const urlPromises = files.map(async (filePath): Promise<{ url: string; filePath: string } | undefined> => {
        try {
          const base64Data = await window.sculptor!.getFileData(filePath);
          return { url: base64Data, filePath };
        } catch (error) {
          console.error("Failed to load file:", filePath, error);
          failed.add(filePath);
          return undefined;
        }
      });

      const urls = await Promise.all(urlPromises);
      const validUrls = urls.filter((item): item is { url: string; filePath: string } => item !== null);

      const urlsMap: Record<string, string> = {};
      for (const { filePath, url } of validUrls) {
        urlsMap[filePath] = url;
      }
      setFilesUrls(urlsMap);
      setFailedFiles(failed);
    };

    loadFiles();
  }, [files]);

  if (files.length === 0) {
    return undefined;
  }

  return (
    <Flex gap="2" wrap="wrap" px="2" data-testid={ElementIds.FILE_PREVIEW_LIST}>
      {files.map((filePath) => {
        const fileUrl = filesUrls[filePath];
        const isFailed = failedFiles.has(filePath);
        const isPdf = isPdfFile(filePath);
        const fileName = getFileName(filePath);

        return (
          <FilePreview
            key={filePath}
            filePath={filePath}
            fileUrl={fileUrl}
            isFailed={isFailed}
            isPdf={isPdf}
            fileName={fileName}
            onRemove={onRemoveFile ? (): void => onRemoveFile(filePath) : undefined}
            onError={() => {
              setFailedFiles((prev) => new Set(prev).add(filePath));
            }}
          />
        );
      })}
    </Flex>
  );
};
