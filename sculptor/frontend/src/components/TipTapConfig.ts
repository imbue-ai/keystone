// import { Extension } from "@tiptap/core";
import CodeBlockLowlight from "@tiptap/extension-code-block-lowlight";
import Mention from "@tiptap/extension-mention";
import Placeholder from "@tiptap/extension-placeholder";
import { Extension } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { common, createLowlight } from "lowlight";
import { Markdown } from "tiptap-markdown";

import styles from "./Editor.module.scss";
import { createFileSuggestion, createSlashCommandSuggestion } from "./SuggestionUtils";

const lowlight = createLowlight(common);

/**
 * Escapes bullet points at the beginning of lines to prevent parsing conflicts.
 * When a user types "- task description", it should be escaped as "\- task description"
 * to avoid being interpreted as markdown bullets in downstream processing.
 */
export const escapeBulletPoints = (text: string): string => {
  // Split into lines, escape bullet points at the start of each line, then rejoin
  return text
    .split("\n")
    .map((line) => {
      // If line starts with "- " (bullet point), escape it to "\- "
      if (line.startsWith("- ")) {
        return line.replace(/^- /, "\\- ");
      }
      return line;
    })
    .join("\n");
};

type TipTapConfigOptions = {
  placeholder?: string;
  editable?: boolean;
  containerElement?: HTMLElement | null;
  projectID?: string;
  taskID?: string;
};

/**
 * Creates the shared TipTap extensions configuration used by both Editor and TipTapViewer
 */
export const createTipTapExtensions = ({
  placeholder,
  editable = true,
  containerElement,
  projectID,
  taskID,
}: TipTapConfigOptions): Array<Extension> => {
  const extensions = [
    StarterKit.configure({
      codeBlock: false,
    }),
    Markdown.configure({
      tightListClass: styles.tightList,
      transformCopiedText: true,
      transformPastedText: true,
    }),
    CodeBlockLowlight.configure({ lowlight }) as Extension<unknown, unknown>,
    Mention.configure({
      ...(editable && containerElement && projectID
        ? {
            suggestions: [
              createFileSuggestion(containerElement, projectID),
              // Only show commands if we already have a task at hand.
              // If we're just creating one, we don't know if it ends up using Claude or Codex or something else yet.
            ].concat(taskID !== undefined ? [createSlashCommandSuggestion(containerElement, projectID, taskID)] : []),
          }
        : {}),
      HTMLAttributes: {
        class: styles.mention,
      },
      renderHTML({ options, node }) {
        return ["span", options.HTMLAttributes, `${node.attrs.id}`];
      },
      deleteTriggerWithBackspace: true,
    }) as Extension<unknown, unknown>,
    Extension.create({
      name: "PreventEnter",
      addKeyboardShortcuts() {
        return {
          "Mod-Enter": (): boolean => true, // Just return true, nothing else
        };
      },
    }),
  ];

  // Only add placeholder for editable mode
  if (editable && placeholder) {
    extensions.push(
      Placeholder.configure({
        placeholder,
        emptyNodeClass: styles.placeholder,
      }),
    );
  }

  return extensions;
};
