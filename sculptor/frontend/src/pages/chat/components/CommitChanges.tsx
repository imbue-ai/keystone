import { Button, Flex, Skeleton, TextArea } from "@radix-ui/themes";
import { useAtomValue } from "jotai";
import type { ReactElement } from "react";
import { useState } from "react";

import { ElementIds, gitCommitInTask } from "~/api";
import { useTaskPageParams } from "~/common/NavigateUtils.ts";
import { useModifiedEnter } from "~/common/ShortcutUtils.ts";
import { doesSendMessageShortcutIncludeModifierAtom } from "~/common/state/atoms/userConfig.ts";
import { useTask } from "~/common/state/hooks/useTaskHelpers.ts";

import styles from "./CommitChanges.module.scss";

export const CommitChanges = (): ReactElement => {
  // TODO: use persistence on this field
  const [commitMessage, setCommitMessage] = useState("");
  const [isCommitPushLoading, setIsCommitPushLoading] = useState(false);
  const doesSendMessageShortcutIncludeModifier = useAtomValue(doesSendMessageShortcutIncludeModifierAtom);

  const { projectID, taskID } = useTaskPageParams();
  const task = useTask(taskID);

  const handleCommit = async (): Promise<void> => {
    if (!commitMessage.trim()) return;
    setIsCommitPushLoading(true);
    try {
      await gitCommitInTask({
        path: { project_id: projectID, task_id: taskID },
        body: { commitMessage: commitMessage, is_awaited: true },
      });
      setCommitMessage("");
      // TODO: bring back toast (need to make a global toast consumer)
      // setSyncToast({ title: "Git commit and push completed successfully", type: ToastType.SUCCESS });
    } catch (error) {
      console.error("Failed to perform git commit and push:", error);
      // TODO: bring back toast (need to make a global toast consumer)
      // setSyncToast({ title: "Failed to perform git commit and push", type: ToastType.ERROR });
    } finally {
      setIsCommitPushLoading(false);
    }
  };

  const handleKeyPress = useModifiedEnter({
    onConfirm: handleCommit,
    doesSendMessageShortcutIncludeModifier,
  });

  if (!task) {
    return (
      <Flex height="100%" justify="center" align="center">
        <Skeleton />
        <Skeleton />
        <Skeleton />
      </Flex>
    );
  }

  const isAgentWorking = task.status !== "READY";
  const isCommitDisabled = !commitMessage.trim() || isCommitPushLoading || isAgentWorking;

  return (
    <Flex direction="column" gap="2" mt="2" position="relative">
      <TextArea
        placeholder="Enter commit message..."
        value={commitMessage}
        onChange={(e) => setCommitMessage(e.target.value)}
        onKeyDown={(e) => void handleKeyPress(e.nativeEvent)}
        className={styles.commitMessageInput}
        data-testid={ElementIds.GIT_COMMIT_MESSAGE_INPUT}
        style={{
          minHeight: "100px",
        }}
        disabled={isCommitPushLoading}
      />
      <Button
        size="1"
        variant="solid"
        onClick={handleCommit}
        disabled={isCommitDisabled}
        loading={isCommitPushLoading}
        data-is-loading={isCommitPushLoading}
        data-testid={ElementIds.GIT_COMMIT_BUTTON}
        className={styles.commitButton}
      >
        Commit
      </Button>
    </Flex>
  );
};
