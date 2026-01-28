import type { ArtifactView } from "../../Types.ts";
import { ChecksTabLabelComponent, ChecksViewComponent } from "./ChecksArtifactView";
import { CombinedDiffArtifactViewComponent, CombinedDiffTabLabelComponent } from "./CombinedDiffArtifactView";
import { DevScoutArtifactViewComponent, DevScoutTabLabelComponent } from "./DevScoutArtifactView.tsx";
import { DevSuggestionsArtifactViewComponent, DevSuggestionsTabLabelComponent } from "./DevSuggestionsArtifactView";
import { LogsArtifactViewComponent, LogsTabLabelComponent } from "./LogsArtifactView";
import { ScoutArtifactViewComponent, ScoutTabLabelComponent } from "./ScoutArtifactView.tsx";
import { SuggestionsTabLabelComponent, SuggestionsViewComponent } from "./SuggestionsArtifactView";
import { TerminalArtifactViewComponent, TerminalTabLabelComponent } from "./TerminalArtifactView";
import { TodoListArtifactViewComponent, TodoListTabLabelComponent } from "./TodoListArtifactView";

export const todoListArtifactView: ArtifactView = {
  id: "TodoList",
  tabOrder: 0,
  contentComponent: TodoListArtifactViewComponent,
  tabLabelComponent: TodoListTabLabelComponent,
};

const combinedDiffArtifactView: ArtifactView = {
  id: "CombinedDiff",
  tabOrder: 1,
  contentComponent: CombinedDiffArtifactViewComponent,
  tabLabelComponent: CombinedDiffTabLabelComponent,
};

const suggestionsView: ArtifactView = {
  id: "Suggestions",
  tabOrder: 2,
  contentComponent: SuggestionsViewComponent,
  tabLabelComponent: SuggestionsTabLabelComponent,
};

const checksView: ArtifactView = {
  id: "Checks",
  tabOrder: 3,
  contentComponent: ChecksViewComponent,
  tabLabelComponent: ChecksTabLabelComponent,
};

const logsArtifactView: ArtifactView = {
  id: "Logs",
  tabOrder: 4,
  contentComponent: LogsArtifactViewComponent,
  tabLabelComponent: LogsTabLabelComponent,
};

const terminalArtifactView: ArtifactView = {
  id: "Terminal",
  tabOrder: 5,
  contentComponent: TerminalArtifactViewComponent,
  tabLabelComponent: TerminalTabLabelComponent,
};

const devSuggestionsArtifactView: ArtifactView = {
  id: "DevSuggestions",
  tabOrder: 6,
  contentComponent: DevSuggestionsArtifactViewComponent,
  tabLabelComponent: DevSuggestionsTabLabelComponent,
};

const scoutArtifactView: ArtifactView = {
  id: "Scout",
  tabOrder: 7,
  contentComponent: ScoutArtifactViewComponent,
  tabLabelComponent: ScoutTabLabelComponent,
};

const devScoutArtifactView: ArtifactView = {
  id: "DevScout",
  tabOrder: 8,
  contentComponent: DevScoutArtifactViewComponent,
  tabLabelComponent: DevScoutTabLabelComponent,
};

/**
 * The set of all registered artifact views.
 *
 * Any new artifact views should be added to this list.
 */
export const registeredArtifactViews: ReadonlyArray<ArtifactView> = [
  todoListArtifactView,
  combinedDiffArtifactView,
  suggestionsView,
  checksView,
  terminalArtifactView,
  logsArtifactView,
  devSuggestionsArtifactView,
  scoutArtifactView,
  devScoutArtifactView,
];

export const defaultEnabledArtifactViewIds: ReadonlyArray<string> = [
  todoListArtifactView.id,
  combinedDiffArtifactView.id,
  suggestionsView.id,
  checksView.id,
  logsArtifactView.id,
  terminalArtifactView.id,
  devSuggestionsArtifactView.id,
  scoutArtifactView.id,
  devScoutArtifactView.id,
];
