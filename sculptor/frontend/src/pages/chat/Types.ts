import type { ReactElement } from "react";

import type { ImbueCLIActionOutputUnion } from "~/common/Guards";

import type {
  ArtifactType,
  CheckFinishedReason,
  CodingAgentTaskView,
  DiffArtifact,
  LogsArtifact,
  ScoutOutput,
  Suggestion,
  SuggestionsArtifact,
  TodoListArtifact,
  UsageArtifact,
} from "../../api";
import { type Check as ApiCheck } from "../../api";

export type Check = ApiCheck;

// ===============================
// Artifact Types
// ===============================

export type ChecksData = {
  checkHistoryByNameByMessageId: Record<string, Record<string, CheckHistory>>;
};

export type SuggestionWithSource = {
  suggestion: Suggestion;
  runId: string;
  checkName: string;
};

export type SuggestionsData = {
  suggestionsByMessageId: Record<string, Array<SuggestionWithSource>>;
};

export type CheckOutputWithSource = {
  output: ImbueCLIActionOutputUnion;
  runId: string;
  checkName: string;
};

export type NewCheckOutputsData = {
  checkOutputsByMessageId: Record<string, Array<CheckOutputWithSource>>;
};

export type ScoutOutputWithSource = {
  output: ScoutOutput;
  runId: string;
  checkName: string;
};

export type NewScoutOutputsData = {
  scoutOutputsByMessageId: Record<string, Array<ScoutOutputWithSource>>;
};

export type ArtifactsMap = {
  [ArtifactType.DIFF]?: DiffArtifact;
  [ArtifactType.PLAN]?: TodoListArtifact;
  [ArtifactType.SUGGESTIONS]?: SuggestionsArtifact;
  [ArtifactType.LOGS]?: LogsArtifact;
  [ArtifactType.USAGE]?: UsageArtifact;
  [ArtifactType.CHECKS]?: ChecksData;
  [ArtifactType.NEW_CHECK_OUTPUTS]?: NewCheckOutputsData;
};

export type ArtifactViewContentProps = {
  task: CodingAgentTaskView;
  artifacts: ArtifactsMap;
  checksData?: Record<string, Record<string, CheckHistory>>;
  userMessageIds?: Array<string>;
  appendTextRef?: React.MutableRefObject<((text: string) => void) | null>;
  checksDefinedForMessage?: Set<string>;
};

export type ArtifactViewTabLabelProps = {
  artifacts: ArtifactsMap;
  shouldShowIcon?: boolean;
};

export type ArtifactView = {
  readonly id: string;
  // Priority for ordering in the tabs list
  readonly tabOrder: number;
  // React component for rendering
  contentComponent: (props: ArtifactViewContentProps) => ReactElement;
  // React component for rendering the tab label
  tabLabelComponent: (props: ArtifactViewTabLabelProps) => ReactElement;
};

export type CheckStatus = {
  check: Check;
  startedAt?: number;
  stoppedAt?: number;
  exitCode?: number | null;
  finishedReason?: CheckFinishedReason;
  archivalReason?: string;
};

export type CheckHistory = {
  statusByRunId: Record<string, CheckStatus>;
  runIds: Array<string>;
  checkDefinition?: Check;
};

export type CheckOutputList = {
  checkOutputsByCheckName?: Record<string, Array<CheckOutputWithSource>>;
  currentRunIdByCheckName?: Record<string, string>;
};
