import { Flex, Spinner } from "@radix-ui/themes";
import { useAtomValue } from "jotai";
import { CopyIcon, SplitIcon, ThumbsDown, ThumbsUp } from "lucide-react";
import type { ReactElement } from "react";

import { isForkingBetaFeatureOnAtom } from "~/common/state/atoms/userConfig.ts";

import { ElementIds } from "../../../api";
import { TooltipIconButton } from "../../../components/TooltipIconButton";
import styles from "./FeedbackButtons.module.scss";
import { FeedbackType } from "./FeedbackDialog";

type MessageActionBarProps = {
  onThumbsUp: () => void;
  onThumbsDown: () => void;
  onFork: () => void;
  onCopy: () => void;
  submittedFeedback?: FeedbackType | null;
  snapshotId?: string | null;
  didSnapshotFail?: boolean;
};

export const MessageActionBar = ({
  onThumbsUp,
  onThumbsDown,
  onFork,
  onCopy,
  submittedFeedback,
  snapshotId,
  didSnapshotFail,
}: MessageActionBarProps): ReactElement => {
  const isForkingEnabled = useAtomValue(isForkingBetaFeatureOnAtom);

  return (
    <>
      <Flex direction="row" align="center" gap="2" justify="start" mt="2" data-testid={ElementIds.MESSAGE_ACTION_BAR}>
        <TooltipIconButton tooltipText="Copy message" className={styles.tooltipIconButton} onClick={onCopy}>
          <CopyIcon />
        </TooltipIconButton>
        <TooltipIconButton
          tooltipText="This was helpful"
          onClick={onThumbsUp}
          data-testid={ElementIds.THUMBS_UP_BUTTON}
          icon={<ThumbsUp size={16} fill={submittedFeedback === FeedbackType.POSITIVE ? "currentColor" : "none"} />}
          side="top"
          className={`${styles.tooltipIconButton} ${submittedFeedback === FeedbackType.POSITIVE ? styles.feedbackSubmitted : ""}`}
          data-ph-capture-attribute-semantic_label="task_user_feedback"
          data-ph-capture-attribute-feedback_type={FeedbackType.POSITIVE}
        />
        <TooltipIconButton
          tooltipText="This was not helpful"
          onClick={onThumbsDown}
          data-testid={ElementIds.THUMBS_DOWN_BUTTON}
          icon={<ThumbsDown size={16} fill={submittedFeedback === FeedbackType.NEGATIVE ? "currentColor" : "none"} />}
          side="top"
          className={`${styles.tooltipIconButton} ${submittedFeedback === FeedbackType.NEGATIVE ? styles.feedbackSubmitted : ""}`}
          data-ph-capture-attribute-semantic_label="task_user_feedback"
          data-ph-capture-attribute-feedback_type={FeedbackType.NEGATIVE}
        />
        {isForkingEnabled &&
          (snapshotId ? (
            <TooltipIconButton
              tooltipText="Fork task"
              className={styles.tooltipIconButton}
              onClick={onFork}
              data-testid={ElementIds.FORK_BUTTON}
            >
              <SplitIcon />
            </TooltipIconButton>
          ) : didSnapshotFail ? (
            <TooltipIconButton
              tooltipText="Cannot fork (snapshot failed)"
              className={styles.tooltipIconButton}
              data-testid={ElementIds.FORK_BUTTON_NOT_POSSIBLE}
            >
              X
            </TooltipIconButton>
          ) : (
            <TooltipIconButton
              tooltipText="Fork task (waiting for snapshot to complete)"
              className={styles.tooltipIconButton}
              data-testid={ElementIds.FORK_BUTTON_SPINNER}
            >
              <Spinner size="1" />
            </TooltipIconButton>
          ))}
      </Flex>
    </>
  );
};
