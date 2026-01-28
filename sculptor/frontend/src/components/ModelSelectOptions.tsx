import { Select } from "@radix-ui/themes";
import type { ReactElement } from "react";

import { LlmModel } from "~/api";
import { getModelLongName } from "~/common/modelConstants";
import { getModelAvailability } from "~/common/utils/modelUtils";

import { DisabledModelOption } from "./DisabledModelOption";

type ModelSelectOptionsProps = {
  currentModel: LlmModel | null;
  hasAnthropicCreds: boolean;
  hasOpenAICreds: boolean;
  shouldDisableOptions: boolean;
  optionTestId?: string;
};

/**
 * Renders the model options for a Select dropdown, handling visibility and disabled states
 */
export const ModelSelectOptions = ({
  currentModel,
  hasAnthropicCreds,
  hasOpenAICreds,
  shouldDisableOptions,
  optionTestId,
}: ModelSelectOptionsProps): ReactElement => {
  return (
    <>
      {Object.values(LlmModel).map((modelValue) => {
        const { isDisabled, isHidden, tooltipMessage } = getModelAvailability(
          modelValue,
          currentModel,
          hasAnthropicCreds,
          hasOpenAICreds,
        );

        // Don't render hidden models at all
        if (isHidden) {
          return null;
        }

        if (shouldDisableOptions && isDisabled) {
          return (
            <DisabledModelOption
              key={modelValue}
              modelName={getModelLongName(modelValue)}
              tooltipMessage={tooltipMessage}
              data-testid={optionTestId}
            />
          );
        }

        return (
          <Select.Item key={modelValue} value={modelValue} data-testid={optionTestId}>
            {getModelLongName(modelValue)}
          </Select.Item>
        );
      })}
    </>
  );
};
