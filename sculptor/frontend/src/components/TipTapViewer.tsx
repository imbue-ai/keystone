import { EditorContent, useEditor } from "@tiptap/react";
import type { ReactElement } from "react";

import styles from "./Editor.module.scss";
import { createTipTapExtensions } from "./TipTapConfig";

type TipTapViewerProps = {
  content: string;
  className?: string;
};

/**
 * A read-only TipTap viewer that renders content with the same styling as the Editor
 * but without editing capabilities.
 */
export const TipTapViewer = ({ content, className }: TipTapViewerProps): ReactElement => {
  const editor = useEditor(
    {
      extensions: createTipTapExtensions({
        editable: false,
      }),
      editorProps: {
        attributes: {
          class: `${styles.editor} ${styles.viewer} ${className || ""}`,
        },
        editable: () => false,
      },
      content: content,
      editable: false,
    },
    [content],
  ); // Add content as a dependency to recreate editor when content changes

  return <EditorContent editor={editor} />;
};
