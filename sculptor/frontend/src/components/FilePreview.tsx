import { Flex, IconButton, Text, Tooltip } from "@radix-ui/themes";
import { FileIcon, FileWarningIcon, XIcon } from "lucide-react";
import type { ReactElement } from "react";

import { ElementIds } from "~/api";

import styles from "./FilePreview.module.scss";

type FilePreviewProps = {
  filePath: string;
  fileUrl?: string;
  isFailed: boolean;
  isPdf: boolean;
  fileName: string;
  onRemove?: () => void;
  onError: () => void;
};

export const FilePreview = ({
  filePath,
  fileUrl,
  isFailed,
  isPdf,
  fileName,
  onRemove,
  onError,
}: FilePreviewProps): ReactElement => {
  const renderPreviewContent = (): ReactElement => {
    if (isFailed || !fileUrl) {
      return (
        <Tooltip content="Failed to load file. The file may be corrupted or inaccessible.">
          <Flex align="center" justify="center" className={styles.previewError}>
            <Text size="1" color="red" style={{ textAlign: "center", padding: "4px" }}>
              <FileWarningIcon />
            </Text>
          </Flex>
        </Tooltip>
      );
    }

    if (isPdf) {
      return (
        <Tooltip content={fileName}>
          <Flex align="center" justify="center" direction="column" className={styles.preview} mt="1">
            <FileIcon size={12} />
          </Flex>
        </Tooltip>
      );
    }

    return (
      <img
        src={fileUrl}
        alt={`Attachment: ${fileName}`}
        className={styles.preview}
        onError={onError}
        data-testid={ElementIds.FILE_PREVIEW}
        data-path={filePath}
      />
    );
  };

  return (
    <Flex
      position="relative"
      className={`${isFailed ? styles.previewContainerFailed : styles.previewContainer}`}
      data-testid={ElementIds.FILE_PREVIEW_CONTAINER}
    >
      {renderPreviewContent()}
      {onRemove && (
        <IconButton
          size="1"
          variant="solid"
          onClick={onRemove}
          className={styles.removeButton}
          data-testid={ElementIds.FILE_PREVIEW_REMOVE}
        >
          <XIcon size={12} />
        </IconButton>
      )}
    </Flex>
  );
};
