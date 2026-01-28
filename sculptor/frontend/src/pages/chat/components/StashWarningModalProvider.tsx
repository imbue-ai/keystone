import { AlertDialog, Button, Checkbox, Flex, Text } from "@radix-ui/themes";
import { useAtomValue } from "jotai";
import { FileTextIcon } from "lucide-react";
import type { ReactElement } from "react";
import { useState } from "react";

import type { GitRepoStatus, LocalSyncStatus } from "~/api";
import { isPairingModeStashingBetaFeatureOnAtom } from "~/common/state/atoms/userConfig.ts";
import { useStashWarningPreference } from "~/common/state/hooks/useStashWarningPreference.ts";
import { Code } from "~/components/Code";

import styles from "./StashWarningModalProvider.module.scss";

type StashWarningWrapperProps = {
  // TODO: IDK if this builder pattern is still an idiom
  children: (proceedOrWarn: () => Promise<void>) => ReactElement;
  onProceed: () => Promise<void>;
  repoStatus: GitRepoStatus | undefined;
  localSyncStatus: LocalSyncStatus | undefined;
  currentBranch: string | undefined;
};

const UNKNOWN_STATE_HEADER = "You may have untracked or uncommitted changes. Proceed anyway?";

export const StashWarningModalProvider = ({
  children: buildChild,
  onProceed,
  repoStatus,
  localSyncStatus,
  currentBranch,
}: StashWarningWrapperProps): ReactElement => {
  const [isModalOpen, setIsModalOpen] = useState(false);

  const isPairingModeStashingEnabled = useAtomValue(isPairingModeStashingBetaFeatureOnAtom);
  const { isStashWarningEnabled } = useStashWarningPreference();

  const willStartingSyncUseStashingLogic =
    isPairingModeStashingEnabled && (localSyncStatus === undefined || localSyncStatus === "INACTIVE");
  const isStatusUnknownOrDirty = repoStatus === undefined || !repoStatus.isCleanAndSafeToOperateOn; // intermediate state disables button anyways

  // If the task status somehow changes with the modal open, it becomes misleading.
  // But if the git status changes with it open, it would be surprising for it to vanish,
  // so instead we delegate signaling that to the internal rendering
  const shouldShowWarning =
    isStashWarningEnabled && willStartingSyncUseStashingLogic && (isModalOpen || isStatusUnknownOrDirty);

  if (!shouldShowWarning) {
    return buildChild(onProceed);
  }

  return (
    <>
      {buildChild(async () => setIsModalOpen(true))}
      <StashWarningModal
        onClose={() => setIsModalOpen(false)}
        onProceed={onProceed}
        isOpen={isModalOpen}
        repoStatus={repoStatus}
        currentBranch={currentBranch}
      />
    </>
  );
};

type StashWarningModalProps = {
  isOpen: boolean;
  repoStatus: GitRepoStatus | undefined;
  onClose: () => void;
  onProceed: () => Promise<void>;
  currentBranch: string | undefined;
};

const StashWarningModal = ({
  isOpen,
  repoStatus,
  onProceed,
  onClose,
  currentBranch,
}: StashWarningModalProps): ReactElement => {
  const { isStashWarningEnabled, disableStashWarning } = useStashWarningPreference();
  const [doesUserWantStashWarningEnabled, setDoesUserWantStashWarningEnabled] = useState<boolean | null>(null);

  const handleSubmit = async (): Promise<void> => {
    if (doesUserWantStashWarningEnabled != null && isStashWarningEnabled != doesUserWantStashWarningEnabled) {
      await disableStashWarning();
    }
    setDoesUserWantStashWarningEnabled(null);
    onClose();
    await onProceed();
  };

  const handleCancel = (): void => {
    onClose();
    setDoesUserWantStashWarningEnabled(null);
  };

  return (
    <AlertDialog.Root
      open={isOpen}
      onOpenChange={(open) => {
        if (!open) {
          handleCancel();
        }
      }}
    >
      <AlertDialog.Content maxWidth="500px">
        <AlertDialog.Title size="5" weight="bold">
          {deriveHeader(repoStatus)}
        </AlertDialog.Title>
        <AlertDialog.Description size="3" mt="3">
          If you proceed, any untracked and uncommitted changes will be stashed in a ref under{" "}
          <Code className={styles.inlineCode}>refs/sculptor</Code>.
          <div style={{ marginBottom: "0.5em" }} />
          When you stop Paring Mode, sculptor will restore them into{" "}
          {currentBranch !== undefined && currentBranch !== "HEAD" ? (
            <Code className={styles.inlineCode}>{currentBranch}</Code>
          ) : (
            "the current branch"
          )}
          .
        </AlertDialog.Description>
        <Flex align="center" gap="2" mt="4" py="2" px="3" className={styles.repoStatus}>
          <FileTextIcon />
          <Text size="2">{describeStatus(repoStatus)}.</Text>
        </Flex>

        <Flex align="center" gap="2" mt="4">
          <Checkbox
            checked={!(doesUserWantStashWarningEnabled ?? isStashWarningEnabled)}
            onCheckedChange={(checked) => setDoesUserWantStashWarningEnabled(!checked)}
          />
          <Text size="2" color="gray">
            Don&apos;t ask again
          </Text>
        </Flex>

        <Flex gap="3" justify="end" mt="5">
          <AlertDialog.Cancel>
            <Button variant="soft" color="gray" onClick={handleCancel}>
              Cancel
            </Button>
          </AlertDialog.Cancel>
          <AlertDialog.Action>
            <Button
              className={`${styles.proceedButton} ${repoStatus?.isCleanAndSafeToOperateOn ? "" : styles.willStash}`}
              onClick={handleSubmit}
              disabled={repoStatus?.isInIntermediateState}
            >
              Proceed with Sync
            </Button>
          </AlertDialog.Action>
        </Flex>
      </AlertDialog.Content>
    </AlertDialog.Root>
  );
};

const deriveHeader = (repoStatus: GitRepoStatus | undefined): string => {
  if (repoStatus === undefined) {
    return UNKNOWN_STATE_HEADER;
  }

  if (repoStatus.isInIntermediateState) {
    const op = repoStatus.isMerging ? "merge" : repoStatus.isCherryPicking ? "cherry-pick" : "rebase";
    return `Your ${op} operation must be completed before Pairing.`;
  }

  if (repoStatus.isCleanAndSafeToOperateOn) {
    return "Your untracked and uncommitted changes have been resolved.";
  }

  const { deleted, staged, unstaged, untracked } = repoStatus.files;
  const hasUncommitedChanges = deleted + unstaged + staged > 0;
  const hasUntrackedChanges = untracked > 0;
  if (hasUncommitedChanges && hasUntrackedChanges) {
    return "You have untracked and uncommitted changes. Proceed anyway?";
  }

  if (hasUncommitedChanges) {
    return "You have uncommitted changes. Proceed anyway?";
  }

  if (hasUntrackedChanges) {
    return "You have untracked changes. Proceed anyway?";
  }

  // should be impossible but not worth exploding over
  return UNKNOWN_STATE_HEADER;
};

const describeStatus = (repoStatus: GitRepoStatus | undefined): string => {
  if (repoStatus === undefined) {
    return "git status not yet loaded";
  }

  if (repoStatus.isInIntermediateState) {
    return "Can no longer Pair: git repo is in an intermediate state";
  }

  if (repoStatus.files.areCleanIncludingUntracked) {
    return "git status currently clean";
  }

  const untrackedCount = repoStatus?.files.untracked ?? 0;
  const uncommittedCount =
    (repoStatus?.files.unstaged ?? 0) + (repoStatus?.files.staged ?? 0) + (repoStatus?.files.deleted ?? 0);

  const parts: Array<string> = [];
  if (untrackedCount > 0) {
    parts.push(`${untrackedCount} untracked`);
  }

  if (uncommittedCount > 0) {
    parts.push(`${uncommittedCount} uncommitted`);
  }

  if (parts.length === 0) {
    return "changes";
  }
  return `${parts.join(" and ")} change${untrackedCount + uncommittedCount !== 1 ? "s" : ""}`;
};
