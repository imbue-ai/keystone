import { Box, Flex, Select, Text } from "@radix-ui/themes";
import type { ReactElement } from "react";

import { ElementIds } from "../api";
import styles from "./TelemetrySettingsSelector.module.scss";

const telemetryOptions = [
  {
    value: "4",
    label: "Full contribution",
    description: "Error logs, basic usage, and full LLM logs including code, to train Sculptor's agent",
  },
  {
    value: "3",
    label: "Standard",
    description: "Error logs, basic usage, and user chat messages (excludes code and file contents)",
  },
  { value: "2", label: "Essential only", description: "Error logs and basic usage only" },
] as const;

export type TelemetryLevel = (typeof telemetryOptions)[number]["value"];

const labelByValue = Object.fromEntries(telemetryOptions.map((o) => [o.value, o.label])) as Record<
  TelemetryLevel,
  string
>;

type TelemetrySettingsSelectorProps = {
  value: TelemetryLevel;
  onValueChange: (value: TelemetryLevel) => void;
};

/** TelemetrySettings is a subcomponent of the OnboardingWizard to change the value of Telemetry via a drop-down.
 */
export const TelemetrySettingsSelector = ({ value, onValueChange }: TelemetrySettingsSelectorProps): ReactElement => {
  return (
    <Select.Root value={value} onValueChange={(value: string) => onValueChange(value as TelemetryLevel)}>
      <Select.Trigger
        aria-label="Telemetry Level"
        aria-labelledby="telemetryGroupLabel"
        aria-describedby="telemetryGroupDescription"
        data-testid={ElementIds.SETTINGS_TELEMETRY_SELECT}
        variant="soft"
      >
        <Text>{labelByValue[value]}</Text>
      </Select.Trigger>
      <Select.Content position="popper" sideOffset={8} className={styles.telemetryContent}>
        <Select.Group>
          {telemetryOptions.map((opt) => (
            <Select.Item key={opt.value} value={opt.value} className={styles.telemetryItem}>
              <Box className={styles.telemetryBody}>
                <Flex direction="column" gap="2">
                  <Text weight="bold" data-radix-select-item-text data-testid={ElementIds.SETTINGS_TELEMETRY_OPTION}>
                    {opt.label}
                  </Text>
                  <Text size="2" wrap="pretty">
                    {opt.description}
                  </Text>
                </Flex>
              </Box>
            </Select.Item>
          ))}
        </Select.Group>
      </Select.Content>
    </Select.Root>
  );
};
