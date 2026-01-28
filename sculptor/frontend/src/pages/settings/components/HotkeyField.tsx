import { Button, Flex, Text } from "@radix-ui/themes";
import { X } from "lucide-react";
import { type ReactElement, useCallback, useEffect, useState } from "react";

import { ElementIds } from "../../../api";
import { formatShortcutForDisplay } from "../../../common/ShortcutUtils.ts";
import styles from "./HotkeyField.module.scss";

type HotkeyState = "idle" | "recording" | "set";

type HotkeyFieldProps = {
  title: string;
  description: string;
  value: string | undefined;
  onSet: (keys: string) => void;
  onClear: () => void;
  elementId?: string;
};

const formatHotkey = (keys: Array<string>): string => {
  return keys
    .map((key) => {
      switch (key) {
        case "Meta":
          return "Cmd";
        case "Control":
          return "Ctrl";
        case "Alt":
          return "Alt";
        case "Shift":
          return "Shift";
        default:
          return key.toUpperCase();
      }
    })
    .join("+");
};

export const HotkeyField = ({
  title,
  description,
  value,
  onSet,
  onClear,
  elementId,
}: HotkeyFieldProps): ReactElement => {
  const [state, setState] = useState<HotkeyState>(value ? "set" : "idle");
  const [recordedKeys, setRecordedKeys] = useState<Array<string>>([]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (state !== "recording") return;

      e.preventDefault();
      e.stopPropagation();

      // Only capture when a non-modifier key is pressed along with modifiers
      const isModifierOnly = ["Meta", "Control", "Alt", "Shift"].includes(e.key);

      if (!isModifierOnly) {
        const keys: Array<string> = [];
        if (e.metaKey) keys.push("Meta");
        if (e.ctrlKey && !e.metaKey) keys.push("Control");
        if (e.altKey) keys.push("Alt");
        if (e.shiftKey) keys.push("Shift");

        // Add the actual key pressed
        keys.push(e.key);

        setRecordedKeys(keys);
        const hotkeyString = formatHotkey(keys);
        onSet(hotkeyString);
        setState("set");
      }
    },
    [state, onSet],
  );

  useEffect(() => {
    if (state === "recording") {
      window.addEventListener("keydown", handleKeyDown);
      return (): void => window.removeEventListener("keydown", handleKeyDown);
    }
  }, [state, handleKeyDown]);

  const handleClick = (): void => {
    if (state === "idle") {
      setState("recording");
      setRecordedKeys([]);
    }
  };

  const handleClear = (): void => {
    setState("idle");
    setRecordedKeys([]);
    onClear();
  };

  return (
    <Flex direction="column" width="100%" py="4" className={styles.settingRow} data-testid={elementId}>
      <Text weight="medium">{title}</Text>
      <Flex justify="between" align="center" gapY="3">
        <Text size="2" className={styles.descriptionText}>
          {description}
        </Text>
        {state === "idle" && (
          <Button variant="soft" onClick={handleClick} data-testid={ElementIds.SETTINGS_HOTKEY_SET_BUTTON}>
            Click to set
          </Button>
        )}
        {state === "recording" && (
          <Flex className={styles.hotkeyRecording} align="center" justify="center" py="2" px="4">
            <Text size="2">Press hotkey</Text>
          </Flex>
        )}
        {state === "set" && (
          <Flex className={styles.hotkeySet} align="center" justify="between" gap="3" py="2" px="4">
            <Text size="2">{formatShortcutForDisplay(value || formatHotkey(recordedKeys))}</Text>
            <Button
              variant="ghost"
              size="1"
              onClick={handleClear}
              className={styles.hotkeyClear}
              data-testid={ElementIds.SETTINGS_HOTKEY_CLEAR_BUTTON}
            >
              <X size={14} />
            </Button>
          </Flex>
        )}
      </Flex>
    </Flex>
  );
};
