import { computePosition, flip, shift } from "@floating-ui/dom";
import type { Editor } from "@tiptap/react";
import { posToDOMRect, ReactRenderer } from "@tiptap/react";
import type { SuggestionOptions, SuggestionProps } from "@tiptap/suggestion";
import type { ComponentProps, ComponentRef } from "react";

import { getAvailableSlashCommands, getFilesAndFolders } from "../api";
import type { SlashCommand } from "../api/types.gen.ts";
import { MentionList } from "./MentionList.jsx";
import { createMentionPlaceholder } from "./MentionPlaceholder";

type MentionListRef = ComponentRef<typeof MentionList>;
type MentionListProps = ComponentProps<typeof MentionList>;

const updatePosition = (editor: Editor, element: HTMLElement): void => {
  const virtualElement = {
    getBoundingClientRect: (): DOMRect =>
      posToDOMRect(editor.view, editor.state.selection.from, editor.state.selection.to),
  };

  computePosition(virtualElement, element, {
    placement: "bottom-start",
    strategy: "absolute",
    middleware: [shift(), flip()],
  }).then(({ x, y, strategy }) => {
    element.style.width = "max-content";
    element.style.position = strategy;
    element.style.left = `${x}px`;
    element.style.top = `${y}px`;
  });
};

class SuggestionItem {
  id: string;
  label: string;

  constructor(id: string, label: string) {
    this.id = id;
    this.label = label;
  }
}

const renderSuggestion =
  (containerElement: HTMLElement, suggestionSubject: string) =>
  (): {
    onStart: (props: SuggestionProps) => void;
    onUpdate: (props: SuggestionProps) => void;
    onKeyDown: ({ event }: { event: KeyboardEvent }) => boolean;
    onExit: () => void;
  } => {
    let reactRenderer: ReactRenderer<MentionListRef, MentionListProps>;
    // Use this to avoid outdated calls to onUpdate after onExit.
    let isActive = false;
    const placeholder = createMentionPlaceholder(suggestionSubject);

    return {
      onStart: (props): void => {
        isActive = true;
        if (!props.clientRect) {
          return;
        }

        const coords = props.editor.view.coordsAtPos(props.editor.state.selection.from);
        placeholder.createPlaceholder(props.query, containerElement, coords);

        reactRenderer = new ReactRenderer(MentionList, {
          props,
          editor: props.editor,
        });

        if (reactRenderer.element instanceof HTMLElement === false) {
          return;
        }

        reactRenderer.element.style.position = "absolute";
        document.querySelector("[data-is-root-theme]")?.appendChild(reactRenderer.element);
        updatePosition(props.editor, reactRenderer.element);
      },

      onUpdate(props): void {
        if (!isActive) {
          return;
        }
        const coords = props.editor.view.coordsAtPos(props.editor.state.selection.from);
        placeholder.createPlaceholder(props.query, containerElement, coords);

        reactRenderer.updateProps(props);

        if (!props.clientRect || reactRenderer.element instanceof HTMLElement === false) {
          return;
        }

        updatePosition(props.editor, reactRenderer.element);
      },

      onKeyDown({ event }): boolean {
        if (event.key === "Escape") {
          reactRenderer.destroy();
          reactRenderer.element.remove();

          return true;
        }

        return reactRenderer.ref?.onKeyDown({ event }) ?? false;
      },

      onExit(): void {
        isActive = false;
        placeholder.cleanup();

        reactRenderer.destroy();
        reactRenderer.element.remove();
      },
    };
  };

export const createFileSuggestion = (
  containerElement: HTMLElement,
  projectID: string,
): Omit<SuggestionOptions, "editor"> => {
  let cachedQuery = "";
  let cachedFilesAndFolders: Array<string> = [];

  return {
    char: "@",
    items: async ({ query }): Promise<Array<SuggestionItem>> => {
      query = query.toLowerCase();

      if (query === "" || !query.includes(cachedQuery)) {
        try {
          const { data } = await getFilesAndFolders({
            path: { project_id: projectID },
            query: { query: query },
          });
          cachedFilesAndFolders = data || [];
        } catch (error) {
          console.error("Error getting files:", error);
          cachedFilesAndFolders = [];
        }
      } else {
        cachedFilesAndFolders = cachedFilesAndFolders.filter((fileOrFolder) =>
          fileOrFolder.toLowerCase().includes(query.toLowerCase()),
        );
      }
      cachedQuery = query;
      return cachedFilesAndFolders.slice(0, 5).map((item) => new SuggestionItem(`@${item}`, item));
    },

    render: renderSuggestion(containerElement, "files"),
  };
};

export const createSlashCommandSuggestion = (
  containerElement: HTMLElement,
  projectID: string,
  taskID: string,
): Omit<SuggestionOptions, "editor"> => {
  let cachedQuery = "";
  let cachedCommands: Array<SlashCommand> = [];

  return {
    char: "/",
    startOfLine: true,
    items: async ({ query }): Promise<Array<SuggestionItem>> => {
      query = query.toLowerCase();
      if (query === "" || !query.includes(cachedQuery)) {
        try {
          const { data } = await getAvailableSlashCommands({
            path: { project_id: projectID, task_id: taskID },
          });
          cachedCommands = data || [];
        } catch (error) {
          console.error("Error getting available slash commands:", error);
          cachedCommands = [];
        }
      } else {
        cachedCommands = cachedCommands.filter((command) => command.value.toLowerCase().includes(query.toLowerCase()));
      }
      cachedQuery = query;
      return cachedCommands.slice(0, 5).map((command) => new SuggestionItem(command.value, command.displayName));
    },

    render: renderSuggestion(containerElement, "commands"),
  };
};
