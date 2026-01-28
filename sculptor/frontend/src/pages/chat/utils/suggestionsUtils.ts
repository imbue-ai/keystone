import type { Suggestion } from "../../../api";
import type { SuggestionsData } from "../Types";

/**
 * Convert severity value to number with fallback
 */
export const parseSeverity = (severity: string | number): number => {
  if (typeof severity === "number") {
    return severity;
  }
  return parseFloat(severity) || 0;
};

/**
 * Decode HTML entities using the browser's built-in parser
 */
export const decodeHtmlEntities = (html: string): string => {
  const textarea = document.createElement("textarea");
  textarea.innerHTML = html;
  return textarea.value;
};

export const filterValidNewSuggestions = (
  newSuggestions: SuggestionsData | null,
): Array<{ suggestion: Suggestion; checkName: string }> => {
  if (!newSuggestions || !newSuggestions.suggestionsByMessageId) return [];

  const allSuggestions = Object.values(newSuggestions.suggestionsByMessageId).flatMap((suggestionWithSources) =>
    suggestionWithSources.map((suggestionWithSource) => ({
      suggestion: suggestionWithSource.suggestion,
      checkName: suggestionWithSource.checkName,
    })),
  );
  return allSuggestions;
};

export const getNewSuggestionsCount = (newSuggestions: SuggestionsData | null): number => {
  return filterValidNewSuggestions(newSuggestions).length;
};
