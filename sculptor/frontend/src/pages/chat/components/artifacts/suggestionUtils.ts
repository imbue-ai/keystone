import { isSuggestion } from "~/common/Guards.ts";

import type { IdentifiedVerifyIssue } from "../../../../api";
import { type ChatMessage, type Suggestion } from "../../../../api";
import { ChatMessageRole } from "../../../../api";
import type { CheckOutputWithSource, NewCheckOutputsData, SuggestionsData, SuggestionWithSource } from "../../Types.ts";
import type { SuggestionAction } from "./suggestionActions";

export type ProcessedSuggestion = {
  title: string;
  description: string;
  severity: string;
  confidence?: number | null;
  actions?: Array<SuggestionAction>;
  originalIssues?: Array<IdentifiedVerifyIssue>;
};

export type ProcessedSuggestionWithSource = {
  suggestion: ProcessedSuggestion;
  checkName: string;
  runId: string;
  userMessageId: string;
};

export const filterSuggestionsFromCheckOutputs = (checkOutputs: NewCheckOutputsData | undefined): SuggestionsData => {
  const checkOutputsByMessageId = checkOutputs?.checkOutputsByMessageId ?? {};
  const suggestionsByMessageId: Record<string, Array<SuggestionWithSource>> = {};
  for (const [messageId, checkOutputArray] of Object.entries(checkOutputsByMessageId)) {
    const suggestions = checkOutputArray.filter((item): item is CheckOutputWithSource & { output: Suggestion } =>
      isSuggestion(item.output),
    );
    if (suggestions.length > 0) {
      suggestionsByMessageId[messageId] = suggestions.map((s) => ({
        suggestion: s.output,
        runId: s.runId,
        checkName: s.checkName,
      }));
    }
  }
  return { suggestionsByMessageId };
};

export const extractUserMessageIds = (chatMessages?: Array<ChatMessage>): Array<string> => {
  if (!chatMessages) return [];

  const userMessageIds = new Set<string>();
  chatMessages
    .filter((message) => message.role === ChatMessageRole.USER)
    .forEach((message) => {
      userMessageIds.add(message.id);
    });

  const sortedUserMessageIds = Array.from(userMessageIds).sort((a, b) => {
    const indexA = chatMessages.findIndex((msg) => msg.id === a);
    const indexB = chatMessages.findIndex((msg) => msg.id === b);
    return indexA - indexB;
  });

  return sortedUserMessageIds;
};

export const convertSuggestionFormat = (
  suggestionWithSource: {
    suggestion: Suggestion;
    checkName: string;
    runId: string;
  },
  userMessageId: string,
): ProcessedSuggestionWithSource => {
  const convertedSuggestion: ProcessedSuggestion = {
    title:
      (suggestionWithSource.suggestion.title || "No title available").charAt(0).toUpperCase() +
      (suggestionWithSource.suggestion.title || "No title available").slice(1),
    description: suggestionWithSource.suggestion.description || "No description available",
    severity: suggestionWithSource.suggestion.severityScore?.toString() || "0",
    confidence: suggestionWithSource.suggestion.confidenceScore,
    actions: suggestionWithSource.suggestion.actions?.map((action) => ({
      object_type: action.objectType,
      content: "content" in action ? (action.content as string) : "url" in action ? (action.url as string) : undefined,
    })),
    originalIssues: suggestionWithSource.suggestion.originalIssues,
  };

  return {
    suggestion: convertedSuggestion,
    checkName: suggestionWithSource.checkName,
    runId: suggestionWithSource.runId,
    userMessageId: userMessageId,
  };
};

export const getSuggestionsForMessage = (
  suggestionsData: SuggestionsData | undefined,
  messageId: string,
): Array<ProcessedSuggestionWithSource> => {
  if (!suggestionsData?.suggestionsByMessageId?.[messageId]) {
    return [];
  }

  const newSuggestions = suggestionsData.suggestionsByMessageId[messageId];
  const converted = newSuggestions.map((suggestionWithSource: SuggestionWithSource) =>
    convertSuggestionFormat(suggestionWithSource, messageId),
  );

  const seen = new Set<string>();
  return converted.filter((suggestion) => {
    const key = `${suggestion.checkName}-${suggestion.runId}-${suggestion.suggestion.title}-${suggestion.suggestion.description}`;
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
};

export const getAllSuggestionsForMessages = (
  suggestionsData: SuggestionsData | undefined,
  messageIds: Array<string>,
): Array<ProcessedSuggestionWithSource> => {
  const allSuggestions: Array<ProcessedSuggestionWithSource> = [];

  for (let i = messageIds.length - 1; i >= 0; i--) {
    const suggestions = getSuggestionsForMessage(suggestionsData, messageIds[i]);
    allSuggestions.push(...suggestions);
  }

  return allSuggestions;
};
