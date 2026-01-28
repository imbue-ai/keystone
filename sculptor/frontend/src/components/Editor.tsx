import { ScrollArea } from "@radix-ui/themes";
import { EditorContent, useEditor } from "@tiptap/react";
import { useAtom } from "jotai";
import type { ReactElement } from "react";
import React, { useEffect, useRef } from "react";
import { useHotkeys } from "react-hotkeys-hook";

import { useImbueParams } from "../common/NavigateUtils";
import { isAttemptingToFocusTaskInputAtom } from "../common/state/atoms/sidebar";
import { mergeClasses, optional } from "../common/Utils.ts";
import styles from "./Editor.module.scss";
import { processAndValidateFiles, saveFiles } from "./FileUploadUtils";
import { createTipTapExtensions, escapeBulletPoints } from "./TipTapConfig";

const isFileValidType = (item: DataTransferItem): boolean => {
  return item.kind === "file" && item.type.startsWith("image/");
};

const extractFilesFromClipboard = (items: DataTransferItemList): Array<File> => {
  const files: Array<File> = [];
  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    if (isFileValidType(item)) {
      const file = item.getAsFile();
      if (file) {
        files.push(file);
      }
    }
  }
  return files;
};

const handlePastedFiles = async (
  files: Array<File>,
  onFilesChange: (files: Array<string>) => void,
  onError: (error: { title: string; description?: string }) => void,
): Promise<void> => {
  try {
    const { validFiles, errors } = await processAndValidateFiles(files);

    if (errors.length > 0) {
      onError({
        title: "Paste Error",
        description: errors.join("\n"),
      });
    }

    if (validFiles.length > 0) {
      const savedFilePaths = await saveFiles(validFiles);
      if (savedFilePaths.length > 0) {
        onFilesChange(savedFilePaths);
      } else {
        onError({ title: "Failed to save pasted files" });
      }
    }
  } catch (error) {
    console.error("Error processing pasted files:", error);
    onError({ title: "Failed to process pasted files" });
  }
};

type EditorProps = {
  tagName: string;
  placeholder: string;
  value: string;
  onChange: React.Dispatch<string>;
  onKeyDown?: (event: KeyboardEvent) => boolean | void;
  wrapperClassName?: string;
  disabled?: boolean;
  autoFocus?: boolean;
  footer?: ReactElement | undefined;
  onFilesChange?: (files: Array<string>) => void;
  onError?: (error: { title: string; description?: string }) => void;
  taskID?: string;
};

export const Editor = ({
  tagName,
  placeholder,
  value,
  onChange,
  onKeyDown,
  wrapperClassName,
  autoFocus = true,
  disabled = false,
  footer,
  onFilesChange,
  onError,
  taskID,
}: EditorProps): ReactElement => {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const { projectID } = useImbueParams();
  const onKeyDownRef = useRef<((event: KeyboardEvent) => boolean | void) | undefined>(onKeyDown);
  const [isAttemptingToFocusTaskInput, setIsAttemptingToFocusTaskInput] = useAtom(isAttemptingToFocusTaskInputAtom);

  useEffect(() => {
    onKeyDownRef.current = onKeyDown;
  }, [onKeyDown]);

  const editor = useEditor(
    {
      extensions: createTipTapExtensions({
        placeholder,
        editable: true,
        containerElement: containerRef.current || document.body,
        projectID,
        taskID,
      }),
      editorProps: {
        attributes: {
          class: styles.editor,
          ["data-testid"]: tagName,
        },
        handleKeyDown: (_, event) => {
          return onKeyDownRef.current?.(event) ?? false;
        },
        handlePaste: (view, event) => {
          // Only handle paste if we have the necessary handlers
          if (!onFilesChange || !onError) {
            return false;
          }

          const items = event.clipboardData?.items;
          if (!items) {
            return false;
          }

          const pastedFiles = extractFilesFromClipboard(items);

          if (pastedFiles.length > 0) {
            event.preventDefault();
            handlePastedFiles(pastedFiles, onFilesChange, onError).catch((error) => {
              console.error("Unexpected error in handlePastedFiles:", error);
            });

            // Prevent default paste behavior
            return true;
          }

          // Let TipTap handle non-file pastes
          return false;
        },
      },
      content: value,
      onUpdate: ({ editor }) => {
        const markdownText = editor.storage.markdown.getMarkdown();
        const escapedText = escapeBulletPoints(markdownText);
        onChange(escapedText);
      },
    },
    [projectID],
  );

  useHotkeys("ctrl+f", () => editor?.commands.focus("end"));

  // Handle autoFocus
  useEffect(() => {
    if (autoFocus && editor) {
      editor.commands.focus("end");
    }
  }, [autoFocus, editor]);

  // Handle focus trigger only for TASK_INPUT editor
  useEffect(() => {
    if (isAttemptingToFocusTaskInput && tagName === "TASK_INPUT" && editor) {
      editor.commands.focus("end");
      // Reset the trigger
      setIsAttemptingToFocusTaskInput(false);
    }
  }, [isAttemptingToFocusTaskInput, tagName, editor, setIsAttemptingToFocusTaskInput]);

  useEffect(() => {
    if (editor) {
      const currentMarkdown = editor.storage.markdown.getMarkdown();
      const escapedCurrentMarkdown = escapeBulletPoints(currentMarkdown);

      // Only update if the escaped content is different from the incoming value
      if (value !== escapedCurrentMarkdown) {
        editor.commands.setContent(value);
      }
    }
  }, [value, editor]);

  // Set initial editable state and update when disabled changes
  useEffect(() => {
    if (editor && editor.isEditable !== !disabled) {
      editor.setEditable(!disabled);
    }
  }, [disabled, editor]);

  return (
    <div
      ref={containerRef}
      className={
        wrapperClassName ? wrapperClassName : mergeClasses(optional(disabled, styles.disabled), styles.editorWrapper)
      }
    >
      <ScrollArea scrollbars="vertical" style={{ maxHeight: 300, height: "auto" }}>
        <EditorContent editor={editor} />
      </ScrollArea>
      {footer && <div className={styles.footer}>{footer}</div>}
    </div>
  );
};
