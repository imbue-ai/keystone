import { ImageIcon } from "lucide-react";
import type { ReactElement } from "react";
import { useRef, useState } from "react";

import { ElementIds } from "~/api";

import { ALLOWED_EXTENSIONS, processAndValidateFiles, saveFiles } from "./FileUploadUtils";
import type { ToastContent } from "./Toast";
import { ToastType } from "./Toast";
import { TooltipIconButton } from "./TooltipIconButton";

type FileUploadProps = {
  files: Array<string>;
  onFilesChange: (files: Array<string>) => void;
  onError: (toast: ToastContent) => void;
  disabled?: boolean;
  color?: string;
};

export const FileUpload = ({
  files,
  onFilesChange,
  onError,
  disabled = false,
  color = "var(--gold-10)",
}: FileUploadProps): ReactElement => {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isUploading, setIsUploading] = useState(false);

  const handleFileUpload = async (event: React.ChangeEvent<HTMLInputElement>): Promise<void> => {
    const filesToUpload = event.target.files;
    if (!filesToUpload || filesToUpload.length === 0) return;

    setIsUploading(true);

    const { validFiles, errors } = await processAndValidateFiles(filesToUpload);

    if (errors.length > 0) {
      const errorMessage = errors.join("\n");
      onError({
        title: "Upload Error",
        description: errorMessage,
        type: ToastType.ERROR,
      });
    }

    if (validFiles.length === 0) {
      setIsUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
      return;
    }

    const savedFilePaths = await saveFiles(validFiles);

    if (savedFilePaths.length > 0) {
      onFilesChange([...files, ...savedFilePaths]);
    } else {
      onError({ title: "Failed to upload files", type: ToastType.ERROR });
    }

    setIsUploading(false);

    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  return (
    <>
      <input
        ref={fileInputRef}
        type="file"
        accept={ALLOWED_EXTENSIONS.join(",")}
        multiple
        onChange={handleFileUpload}
        style={{ display: "none" }}
        data-testid={ElementIds.FILE_UPLOAD}
      />
      <TooltipIconButton
        tooltipText="Attach images"
        variant="ghost"
        size="3"
        onClick={() => fileInputRef.current?.click()}
        disabled={disabled || isUploading}
        loading={isUploading}
        aria-label="Attach images"
        style={{ color }}
      >
        <ImageIcon />
      </TooltipIconButton>
    </>
  );
};
