import type { LlmModel } from "~/api";
import { codexModels } from "~/common/modelConstants.ts";

export type ModelAvailability = {
  isDisabled: boolean;
  isHidden: boolean;
  tooltipMessage: string;
};

/**
 * Determines if a model is available based on credentials and current model
 * @param modelValue - The model to check
 * @param currentModel - The currently selected model
 * @param hasAnthropicCreds - Whether Anthropic credentials exist
 * @param hasOpenAICreds - Whether OpenAI credentials exist
 * @returns Object containing disabled state, hidden state, and tooltip message
 */
export const getModelAvailability = (
  modelValue: LlmModel,
  currentModel: LlmModel | null,
  hasAnthropicCreds: boolean,
  hasOpenAICreds: boolean,
): ModelAvailability => {
  const isCodex = codexModels.includes(modelValue);
  const isClaude = !isCodex;
  const hasCredentials = isCodex ? hasOpenAICreds : hasAnthropicCreds;

  let isDisabled = false;
  let isHidden = false;
  let tooltipMessage = "";

  // Check if we should hide incompatible models when switching
  if (currentModel) {
    const isCurrentCodex = codexModels.includes(currentModel);
    const canSwitch = !((isCurrentCodex && isClaude) || (!isCurrentCodex && isCodex));

    if (!canSwitch) {
      // Hide incompatible models instead of showing them disabled
      isHidden = true;
      return { isDisabled: false, isHidden, tooltipMessage };
    }
  }

  // Check credentials for models that aren't hidden
  if (!hasCredentials) {
    isDisabled = true;
    tooltipMessage = isCodex
      ? "OpenAI API key required. Configure in Settings → Account."
      : "Anthropic credentials required. Configure in Settings → Account.";
  }

  return { isDisabled, isHidden, tooltipMessage };
};
