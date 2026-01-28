import { useMemo } from "react";

import type { ChatMessage } from "../../../../api";
import type { SuggestionsData } from "../../Types.ts";
import { extractUserMessageIds, getSuggestionsForMessage, type ProcessedSuggestionWithSource } from "./suggestionUtils";

export const useUserMessageIds = (chatMessages?: Array<ChatMessage>): Array<string> => {
  return useMemo(() => {
    return extractUserMessageIds(chatMessages);
  }, [chatMessages]);
};

export const useSuggestionsForMessage = (
  newSuggestionsData: SuggestionsData,
  messageId: string,
): Array<ProcessedSuggestionWithSource> => {
  return useMemo(() => {
    return getSuggestionsForMessage(newSuggestionsData, messageId);
  }, [newSuggestionsData, messageId]);
};

export const useMostRecentSuggestions = (
  suggestionsData: SuggestionsData | undefined,
  chatMessages?: Array<ChatMessage>,
): Array<ProcessedSuggestionWithSource> => {
  return useMemo(() => {
    const userMessageIds = extractUserMessageIds(chatMessages);

    if (userMessageIds.length === 0) {
      return [];
    }

    const mostRecentMessageId = userMessageIds[userMessageIds.length - 1];
    return getSuggestionsForMessage(suggestionsData, mostRecentMessageId);
  }, [suggestionsData, chatMessages]);
};
