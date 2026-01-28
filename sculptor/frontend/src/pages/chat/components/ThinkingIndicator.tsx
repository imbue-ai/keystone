import { Button, Flex, Spinner, Text, Tooltip } from "@radix-ui/themes";
import { CircleStop } from "lucide-react";
import { type ReactElement, useState } from "react";

import { ElementIds, interruptTask } from "../../../api";
import { THINKING_INDICATOR_ELEMENT_ID } from "../../../common/Constants.ts";
import { useImbueParams } from "../../../common/NavigateUtils.ts";
import { PulsingCircle } from "../../../components/PulsingCircle.tsx";
import { Toast, type ToastContent, ToastType } from "../../../components/Toast.tsx";
import styles from "./ThinkingIndicator.module.scss";

export const ThinkingIndicator = (): ReactElement => {
  const { projectID, taskID } = useImbueParams();
  const [isStoppingTask, setIsStoppingTask] = useState(false);
  const [toast, setToast] = useState<ToastContent | null>(null);

  if (!projectID) {
    throw new Error("Expected projectID to be defined");
  }

  if (!taskID) {
    throw new Error("Expected taskID to be defined");
  }

  const handleStopTask = async (): Promise<void> => {
    setIsStoppingTask(true);

    try {
      await interruptTask({
        path: { project_id: projectID, task_id: taskID },
      });
      setToast({ title: "Task stopped successfully", type: ToastType.SUCCESS });
    } catch (error) {
      console.error("Failed to interrupt task:", error);
      setToast({ title: "Failed to stop task", type: ToastType.ERROR });
    }

    setIsStoppingTask(false);
  };

  return (
    <>
      <Flex align="center" justify="between" direction="row" wrap="nowrap">
        <Flex
          align="center"
          justify="start"
          gap="9px"
          className={styles.thinkingIndicator}
          id={THINKING_INDICATOR_ELEMENT_ID}
        >
          <PulsingCircle />
          <Text size="2">Thinking...</Text>
        </Flex>
        <Flex align="center" gap="1">
          <Tooltip content={isStoppingTask ? "Stopping..." : "Stop generation"}>
            <Button
              aria-label="Stop generation"
              className="stop-button"
              size="1"
              onClick={handleStopTask}
              variant="soft"
              data-testid={ElementIds.STOP_BUTTON}
              disabled={isStoppingTask}
            >
              {isStoppingTask ? (
                <>
                  <Spinner size="1" data-testid={ElementIds.STOP_BUTTON_SPINNER} />
                  Stopping...
                </>
              ) : (
                <>
                  <CircleStop size={16} />
                  Stop
                </>
              )}
            </Button>
          </Tooltip>
        </Flex>
      </Flex>
      <Toast open={!!toast} onOpenChange={(open) => !open && setToast(null)} title={toast?.title} type={toast?.type} />
    </>
  );
};
