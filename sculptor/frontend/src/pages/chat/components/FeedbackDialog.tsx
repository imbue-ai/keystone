import { Button, Dialog, Flex, Select, Text, TextArea } from "@radix-ui/themes";
import type { ReactElement } from "react";
import { useState } from "react";

import { ElementIds } from "../../../api";
import styles from "./FeedbackDialog.module.scss";

export const FeedbackType = {
  POSITIVE: "positive",
  NEGATIVE: "negative",
} as const;

export type FeedbackType = (typeof FeedbackType)[keyof typeof FeedbackType];

export const FeedbackIssueType = {
  UI_BUG: "UI / UX bug",
  BAD_RESPONSE: "Bad response",
  OTHER: "Other",
} as const;

export type FeedbackIssueType = (typeof FeedbackIssueType)[keyof typeof FeedbackIssueType];

const feedbackIssues = Object.entries(FeedbackIssueType) as Array<[FeedbackIssueType, string]>;

type FeedbackDialogProps = {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (feedbackType: FeedbackType, comment: string, issueType?: FeedbackIssueType) => void;
  feedbackType?: FeedbackType;
};

export const FeedbackDialog = ({ isOpen, onClose, onSubmit, feedbackType }: FeedbackDialogProps): ReactElement => {
  const selectedFeedbackType = feedbackType || FeedbackType.POSITIVE;
  const [comment, setComment] = useState("");
  const [selectedIssueType, setSelectedIssueType] = useState<FeedbackIssueType | undefined>(undefined);

  const handleSubmit = (): void => {
    const issueType = selectedFeedbackType === FeedbackType.NEGATIVE ? selectedIssueType : undefined;
    onSubmit(selectedFeedbackType, comment, issueType);
    setComment("");
    setSelectedIssueType(undefined);
    onClose();
  };

  const handleCancel = (): void => {
    setComment("");
    setSelectedIssueType(undefined);
    onClose();
  };

  return (
    <Dialog.Root open={isOpen} onOpenChange={onClose}>
      <Dialog.Content className={styles.dialogContent} data-testid={ElementIds.FEEDBACK_DIALOG}>
        <Dialog.Title>Feedback</Dialog.Title>

        <Flex direction="column" gap="4">
          {selectedFeedbackType === FeedbackType.NEGATIVE ? (
            <Flex direction="column" gap="2">
              <Text size="2">What type of issue do you wish to report? (optional)</Text>
              <Select.Root
                value={selectedIssueType}
                onValueChange={(value) => setSelectedIssueType(value as FeedbackIssueType)}
              >
                <Select.Trigger
                  placeholder="Select issue type..."
                  data-testid={ElementIds.FEEDBACK_DIALOG_ISSUE_TYPE_DROPDOWN}
                />
                <Select.Content>
                  {feedbackIssues.map(([key, label]) => (
                    <Select.Item key={key} value={key}>
                      {label}
                    </Select.Item>
                  ))}
                </Select.Content>
              </Select.Root>
            </Flex>
          ) : (
            <></>
          )}

          {/* Comment Input */}
          <Flex direction="column" gap="2">
            <Text size="2">Please provide details (optional):</Text>
            <TextArea
              placeholder={
                selectedFeedbackType === FeedbackType.POSITIVE
                  ? "What was satisfying about this response?"
                  : "Tell us more about your experience with this response..."
              }
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              rows={4}
              className={styles.commentArea}
            />
          </Flex>

          <Flex>
            <Text size="1" color="gold">
              Submitting this report will send everything in the current task to Imbue for future improvements to our
              models and software, regardless of your default sharing settings. If you do not want this, please do not
              submit.{" "}
              <a href="https://imbue.com/privacy/" target="_blank" rel="noreferrer" className={styles.privacyLink}>
                <Text weight="bold">Learn more</Text>
              </a>
            </Text>
          </Flex>

          {/* Action Buttons */}
          <Flex gap="2" justify="end">
            <Button
              variant="soft"
              color="gray"
              onClick={handleCancel}
              data-testid={ElementIds.FEEDBACK_DIALOG_CANCEL_BUTTON}
            >
              Cancel
            </Button>
            <Button
              onClick={handleSubmit}
              data-testid={ElementIds.FEEDBACK_DIALOG_SUBMIT_BUTTON}
              className={styles.submitButton}
              data-ph-capture-attribute-semantic_label="task_user_feedback"
              data-ph-capture-attribute-feedback_type={selectedFeedbackType}
              data-ph-capture-attribute-feedback_issue_type={selectedIssueType}
              data-ph-capture-attribute-feedback_comment={comment}
            >
              Submit
            </Button>
          </Flex>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
};
