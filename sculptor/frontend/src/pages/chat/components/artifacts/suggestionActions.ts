import type { ToastContent } from "../../../../components/Toast.tsx";
import { ToastType } from "../../../../components/Toast.tsx";

export type SuggestionAction = {
  object_type?: string;
  content?: string;
};

export const extractUseActionContent = (description: string, actions?: Array<SuggestionAction>): string => {
  const useAction = actions?.find((action) => action.object_type === "UseSuggestionAction") as
    | { content?: string }
    | undefined;
  return useAction?.content || description;
};

export const handleSuggestionUse = (
  description: string,
  actions: Array<SuggestionAction> | undefined,
  appendTextRef?: React.MutableRefObject<((text: string) => void) | null>,
): void => {
  const textToAppend = extractUseActionContent(description, actions);

  if (appendTextRef?.current) {
    appendTextRef.current("\n" + textToAppend + "\n");
  } else {
    navigator.clipboard.writeText(textToAppend);
  }
};

export const handleSuggestionCopy = (
  description: string,
  actions: Array<SuggestionAction> | undefined,
  onSuccess?: (toast: ToastContent) => void,
): void => {
  const textToCopy = extractUseActionContent(description, actions);
  navigator.clipboard.writeText(textToCopy);
  onSuccess?.({ title: "Suggestion copied to clipboard", type: ToastType.SUCCESS });
};
