import { type ChatMessage } from "../../../api";

export const groupMessages = (messages: Array<ChatMessage>): Array<ChatMessage> => {
  const grouped: Array<ChatMessage> = [];

  messages
    .filter((message) => message.content !== undefined)
    .forEach((message): void => {
      const lastGrouped = grouped[grouped.length - 1];

      if (!lastGrouped || lastGrouped.role !== message.role) {
        grouped.push(message);
      } else {
        grouped[grouped.length - 1] = {
          ...lastGrouped,
          content: [...lastGrouped.content, ...message.content],
        };
      }
    });
  return grouped;
};

export const DIFF_TOOLS = ["Edit", "MultiEdit", "Write"] as const;
type DiffTool = (typeof DIFF_TOOLS)[number];

export const isDiffTool = (toolName: string): toolName is DiffTool => {
  return DIFF_TOOLS.includes(toolName as DiffTool);
};

export const isImbueCLITool = (toolName: string): boolean => {
  return toolName.startsWith("mcp__imbue");
};

export const getToolDisplayName = (name: string): string => {
  const displayNames: Record<string, string> = {
    Read: "Read file",
    LS: "Listed files",
    Bash: "Ran command",
    TodoWrite: "Edited plan",
    TodoRead: "Read plan",
    Grep: "Searched files",
    Glob: "Found files",
    Edit: "Edited file",
    MultiEdit: "Edited files",
    Write: "Created file",
    mcp__imbue__verify: "Verified code",
    mcp__imbue__check: "Checked code",
    mcp__imbue_tools__retrieve: "Found files",
  };
  return displayNames[name] || name;
};

export const getToolDisplayNamePresent = (name: string): string => {
  const displayNames: Record<string, string> = {
    Read: "Reading file...",
    LS: "Listing files...",
    Bash: "Running command...",
    TodoWrite: "Editing plan...",
    TodoRead: "Reading plan...",
    Grep: "Searching files...",
    Glob: "Finding files...",
    Edit: "Editing file...",
    MultiEdit: "Editing files...",
    Write: "Creating file...",
    mcp__imbue__verify: "Verifying code...",
    WebFetch: "Fetching web content...",
    WebSearch: "Searching web...",
    NotebookRead: "Reading notebook...",
    NotebookEdit: "Editing notebook...",
    Task: "Running task...",
    mcp__imbue_tools__retrieve: "Finding files...",
  };
  return displayNames[name] || `Running ${name}...`;
};

export const getElementIdForMessage = (message: ChatMessage): string => {
  return `${message.role}-message-${message.id}`;
};

export const getCodeLines = (fileContents: string, startLine: number, endLine: number): Array<string> => {
  const lines = fileContents.split("\n");
  return lines.slice(startLine - 1, endLine);
};
