import type {
  CheckFailedIssue,
  CheckFinishedRunnerMessage,
  CheckLaunchedRunnerMessage,
  ChecksDefinedRunnerMessage,
  CommandBlock,
  CommandHtmlOutput,
  CommandTextOutput,
  ContextSummaryBlock,
  DiffArtifact,
  DiffToolContent,
  DownStatus,
  ErrorBlock,
  ErroredOutput,
  FileBlock,
  ForkedFromBlock,
  ForkedToBlock,
  GenericToolContent,
  GetArtifactDataResponse,
  IdentifiedIssue,
  ImbueCliToolContent,
  LogsArtifact,
  OkStatus,
  ResumeResponseBlock,
  RetrieveOutput,
  ScoutOutput,
  Suggestion,
  SuggestionsArtifact,
  TextBlock,
  TodoListArtifact,
  ToolResultBlock,
  ToolUseBlock,
  UsageArtifact,
  WarningBlock,
} from "../api";
import { LlmModel } from "../api";

// Imbue CLI action output type guards
// Should match ActionOutputUnion in imbue_core.imbue_cli.action
export type ImbueCLIActionOutputUnion = CheckFailedIssue | Suggestion | IdentifiedIssue | ScoutOutput | RetrieveOutput;

// Should match UserDisplayOutputUnion in imbue_core.imbue_cli.action
export type ImbueCLIUserDisplayUnion = ErroredOutput | CommandTextOutput | CommandHtmlOutput;

export const isErroredOutput = (response: ImbueCLIUserDisplayUnion): response is ErroredOutput => {
  return response.objectType === "ErroredOutput";
};

export const isCommandTextOutput = (response: ImbueCLIUserDisplayUnion): response is CommandTextOutput => {
  return response.objectType === "CommandTextOutput";
};

export const isCommandHTMLOutput = (response: ImbueCLIUserDisplayUnion): response is CommandHtmlOutput => {
  return response.objectType === "CommandHTMLOutput";
};

export const isSuggestion = (response: ImbueCLIActionOutputUnion): response is Suggestion => {
  return response.objectType === "Suggestion";
};

export const isScoutOutput = (response: ImbueCLIActionOutputUnion): response is ScoutOutput => {
  return response.objectType === "ScoutOutput";
};

// Artifact type guards
export const isDiffArtifact = (response: GetArtifactDataResponse): response is DiffArtifact => {
  return response.objectType === "DiffArtifact";
};

export const isTodoListArtifact = (response: GetArtifactDataResponse): response is TodoListArtifact => {
  return response.objectType === "TodoListArtifact";
};

export const isLogsArtifact = (response: GetArtifactDataResponse): response is LogsArtifact => {
  return response.objectType === "LogsArtifact";
};

export const isSuggestionsArtifact = (response: GetArtifactDataResponse): response is SuggestionsArtifact => {
  return response.objectType === "SuggestionsArtifact";
};

export const isUsageArtifact = (response: GetArtifactDataResponse): response is UsageArtifact => {
  return response.objectType === "UsageArtifact";
};

// Check message type guards
export type Message = {
  objectType?: string;
  messageId?: string;
};

export const isChecksDefinedRunnerMessage = (message: Message): message is ChecksDefinedRunnerMessage => {
  return message.objectType === "ChecksDefinedRunnerMessage";
};

export const isCheckLaunchedRunnerMessage = (message: Message): message is CheckLaunchedRunnerMessage => {
  return message.objectType === "CheckLaunchedRunnerMessage";
};

export const isCheckFinishedRunnerMessage = (message: Message): message is CheckFinishedRunnerMessage => {
  return message.objectType === "CheckFinishedRunnerMessage";
};

export type BlockUnion =
  | TextBlock
  | ToolUseBlock
  | ToolResultBlock
  | ErrorBlock
  | WarningBlock
  | CommandBlock
  | ContextSummaryBlock
  | ResumeResponseBlock
  | ForkedFromBlock
  | ForkedToBlock
  | FileBlock;

// Content block type guards
export const isTextBlock = (content: BlockUnion): content is TextBlock => {
  return content.type === "text";
};

export const isCommandBlock = (content: BlockUnion): content is CommandBlock => {
  return content.type === "command";
};

export const isToolUseBlock = (content: BlockUnion): content is ToolUseBlock => {
  return content.type === "tool_use";
};

export const isToolResultBlock = (content: BlockUnion): content is ToolResultBlock => {
  return content.type === "tool_result";
};

export const isErrorBlock = (content: BlockUnion): content is ErrorBlock => {
  return content.type === "error";
};

export const isWarningBlock = (content: BlockUnion): content is WarningBlock => {
  return content.type === "warning";
};

export const isContextSummaryBlock = (content: BlockUnion): content is ContextSummaryBlock => {
  return content.type === "context_summary";
};

export const isResumeResponseBlock = (content: BlockUnion): content is ResumeResponseBlock => {
  return content.type === "resume_response";
};

export const isForkedFromBlock = (content: BlockUnion): content is ForkedFromBlock => {
  return content.type === "forked_from";
};

export const isForkedToBlock = (content: BlockUnion): content is ForkedToBlock => {
  return content.type === "forked_to";
};

export const isFileBlock = (content: BlockUnion): content is FileBlock => {
  return content.type === "file";
};

// Tool result content type guards

export const isGenericToolContent = (
  content: GenericToolContent | DiffToolContent | ImbueCliToolContent,
): content is GenericToolContent => {
  return content.contentType === "generic";
};

export const isDiffToolContent = (
  content: GenericToolContent | DiffToolContent | ImbueCliToolContent,
): content is DiffToolContent => {
  return content.contentType === "diff";
};

export const isImbueCliToolContent = (
  content: GenericToolContent | DiffToolContent | ImbueCliToolContent,
): content is ImbueCliToolContent => {
  return content.contentType === "imbue_cli";
};

// Provider status type guards

export const isDownStatus = (status: OkStatus | DownStatus): status is DownStatus => {
  return status.objectType === "DownStatus";
};

export const isLlmModel = (value: string): value is LlmModel => {
  return Object.values(LlmModel).includes(value as LlmModel);
};
