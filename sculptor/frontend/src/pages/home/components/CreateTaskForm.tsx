import { Button, Flex, Select, Spinner, Text, Tooltip } from "@radix-ui/themes";
import { useAtomValue, useSetAtom } from "jotai";
import { ArrowRightIcon, BotIcon, ScrollTextIcon } from "lucide-react";
import type { ReactElement } from "react";
import { useCallback } from "react";
import { useEffect, useMemo, useState } from "react";

import { getModelCapabilities } from "~/common/modelCapabilities.ts";
import { codexModels, getModelShortName } from "~/common/modelConstants.ts";
import { useModelCredentials } from "~/common/state/hooks/useModelCredentials.ts";
import { BranchSelector } from "~/components/BranchSelector.tsx";
import { FilePreviewList } from "~/components/FilePreviewList.tsx";
import { ModelSelectOptions } from "~/components/ModelSelectOptions.tsx";

import type { LlmModel } from "../../../api";
import { ElementIds, startTask, updateDefaultSystemPrompt } from "../../../api";
import { useProjectPageParams } from "../../../common/NavigateUtils.ts";
import { useModifiedEnter } from "../../../common/ShortcutUtils.ts";
import {
  defaultModelAtom,
  doesSendMessageShortcutIncludeModifierAtom,
  lastUsedModelAtom,
} from "../../../common/state/atoms/userConfig.ts";
import { useNewTaskDraftAttachedFiles } from "../../../common/state/hooks/useDraftAttachedFiles.ts";
import { useProject } from "../../../common/state/hooks/useProjects.ts";
import { useNewTaskPromptDraft } from "../../../common/state/hooks/usePromptDraft.ts";
import { useRepoInfo } from "../../../common/state/hooks/useRepoInfo.ts";
import { Editor } from "../../../components/Editor.tsx";
import { FileUpload } from "../../../components/FileUpload.tsx";
import { Toast, type ToastContent, ToastType } from "../../../components/Toast.tsx";
import { TooltipIconButton } from "../../../components/TooltipIconButton.tsx";
import styles from "./CreateTaskForm.module.scss";
import { SettingsDialog } from "./SettingsDialog.tsx";

export const CreateTaskForm = (): ReactElement => {
  const { projectID } = useProjectPageParams();
  const project = useProject(projectID);

  const [newTaskPromptDraft, setNewTaskPromptDraft] = useNewTaskPromptDraft(projectID);
  const [attachedFiles, setAttachedFiles] = useNewTaskDraftAttachedFiles(projectID);
  const [userSelectedBranch, setUserSelectedBranch] = useState<string | null>(null);
  const defaultModelPreference = useAtomValue(defaultModelAtom);
  const [model, setModel] = useState<LlmModel>(defaultModelPreference as LlmModel);
  const setLastUsedModel = useSetAtom(lastUsedModelAtom);
  const [isPending, setIsPending] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const doesSendMessageShortcutIncludeModifier = useAtomValue(doesSendMessageShortcutIncludeModifierAtom);
  const [toast, setToast] = useState<ToastContent | null>(null);
  const { repoInfo, fetchRepoInfo, fetchCurrentBranch } = useRepoInfo(projectID);
  const { hasAnthropicCreds, hasOpenAICreds, refetch: refetchCredentials } = useModelCredentials();

  // Get model capabilities
  const modelCapabilities = getModelCapabilities(model);

  const defaultSystemPrompt = project?.defaultSystemPrompt;

  const includeUncommittedLabel = "*";
  const sourceBranch = useMemo(() => {
    if (userSelectedBranch) {
      return userSelectedBranch;
    }

    if (!repoInfo?.currentBranch) {
      return undefined;
    }
    return repoInfo.currentBranch + includeUncommittedLabel;
  }, [userSelectedBranch, repoInfo]);

  useEffect(() => {
    fetchCurrentBranch();
    fetchRepoInfo();
    setUserSelectedBranch(null);
    refetchCredentials();
    setModel(defaultModelPreference as LlmModel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refetchCredentials, projectID, defaultModelPreference]);

  const sendMessageTooltipContent = useMemo((): string | null => {
    if (!repoInfo) {
      return "Loading repository info...";
    }

    if (defaultSystemPrompt === undefined) {
      return "Loading default system prompt...";
    }

    if (isPending) {
      return "Task is being created...";
    }

    if (!newTaskPromptDraft) {
      return "Please enter a task description";
    }
    return null;
  }, [repoInfo, defaultSystemPrompt, isPending, newTaskPromptDraft]);

  const handleSaveDefaultPrompt = async (prompt: string): Promise<void> => {
    try {
      await updateDefaultSystemPrompt({
        path: { project_id: projectID },
        body: { defaultSystemPrompt: prompt },
      });
      setIsSettingsOpen(false);
      setToast({ title: "Default system prompt updated successfully", type: ToastType.SUCCESS });
    } catch (error) {
      console.error("Failed to update default system prompt:", error);
      setToast({ title: "Failed to update default system prompt", type: ToastType.ERROR });
    }
  };

  const handleStartTask = useCallback(async (): Promise<void> => {
    if (isPending || !newTaskPromptDraft) {
      return;
    }

    // Validate model availability before starting task
    const isCodex = codexModels.includes(model);
    if (isCodex) {
      if (!hasOpenAICreds) {
        setToast({ title: "OpenAI credentials required for Codex", type: ToastType.ERROR });
        return;
      }
    } else {
      if (!hasAnthropicCreds) {
        setToast({ title: "Anthropic credentials required for Claude models", type: ToastType.ERROR });
        return;
      }
    }

    setIsPending(true);

    try {
      await startTask({
        body: {
          prompt: newTaskPromptDraft,
          interface: "API",
          sourceBranch:
            sourceBranch?.indexOf(includeUncommittedLabel) === -1 ? sourceBranch : sourceBranch?.split("*")?.[0],
          isIncludingUncommittedChanges: sourceBranch?.indexOf(includeUncommittedLabel) !== -1,
          model: model,
          files: attachedFiles,
        },
        path: { project_id: projectID },
      });
      setIsPending(false);
      setNewTaskPromptDraft(null);
      setAttachedFiles([]);
    } catch (error) {
      console.error("Failed to start task:", error);
      setToast({
        title: "",
        description: (
          <div>
            <b>Failed to start task</b>
            <br />
            <pre>{"" + error}</pre>
            <br />
            See <a href="https://github.com/imbue-ai/sculptor">help docs</a> for more information.
          </div>
        ),
        type: ToastType.ERROR,
      });
      setIsPending(false);
    }
  }, [
    attachedFiles,
    hasAnthropicCreds,
    hasOpenAICreds,
    model,
    newTaskPromptDraft,
    projectID,
    setAttachedFiles,
    setNewTaskPromptDraft,
    sourceBranch,
    isPending,
  ]);

  const handleKeyPress = useModifiedEnter({
    onConfirm: handleStartTask,
    doesSendMessageShortcutIncludeModifier,
  });

  let branchOptions = repoInfo?.recentBranches || [];
  if (branchOptions && branchOptions.length > 0) {
    branchOptions = [branchOptions[0] + " (including uncommitted changes)"].concat(branchOptions);
  }

  return (
    <>
      <Flex direction="column" gap="3" data-testid={ElementIds.TASK_STARTER}>
        <Editor
          tagName="TASK_INPUT"
          placeholder="Describe your task..."
          value={newTaskPromptDraft || ""}
          onKeyDown={handleKeyPress}
          onChange={setNewTaskPromptDraft}
          disabled={isPending}
          onFilesChange={(newFiles) => setAttachedFiles((prev) => [...prev, ...newFiles])}
          onError={setToast}
          footer={
            attachedFiles.length > 0 ? (
              <FilePreviewList
                files={attachedFiles}
                onRemoveFile={(path): void => setAttachedFiles((prev) => prev.filter((curr) => curr !== path))}
              />
            ) : undefined
          }
        />
        <Flex direction="row" justify="end" align="center" gap="2" wrap="wrap">
          <FileUpload
            files={attachedFiles}
            onFilesChange={setAttachedFiles}
            onError={setToast}
            disabled={isPending || !modelCapabilities.supportsFileAttachments}
            color="var(--gold-11)"
          />
          <TooltipIconButton
            tooltipText={
              !modelCapabilities.supportsSystemPrompt
                ? "System prompts are not supported with this model"
                : "Update system prompt"
            }
            variant="soft"
            onClick={() => setIsSettingsOpen(true)}
            disabled={defaultSystemPrompt === undefined || !modelCapabilities.supportsSystemPrompt}
            size="2"
            aria-label="Toggle system prompt"
            data-testid={ElementIds.HOME_PAGE_SYSTEM_PROMPT_OPEN_BUTTON}
            className={styles.systemPromptIcon}
          >
            <ScrollTextIcon />
          </TooltipIconButton>
          {repoInfo ? (
            <BranchSelector
              fetchRepoInfo={fetchRepoInfo}
              repoInfo={repoInfo}
              setUserSelectedBranch={setUserSelectedBranch}
              sourceBranch={sourceBranch}
            />
          ) : (
            <Button disabled={true} className={styles.sendButton} data-testid={ElementIds.DISABLED_BRANCH_SELECTOR}>
              <Flex align="center" gap="1">
                <Spinner />
                <Text size="1">Loading ...</Text>
              </Flex>
            </Button>
          )}

          <Select.Root
            value={model}
            onValueChange={(value: LlmModel) => {
              setModel(value);
              setLastUsedModel(value);
              // Clear attached files when switching to a model that doesn't support them
              const newCapabilities = getModelCapabilities(value);
              if (!newCapabilities.supportsFileAttachments && attachedFiles.length > 0) {
                setAttachedFiles([]);
              }
            }}
          >
            <Select.Trigger
              placeholder="Select model"
              className={styles.dropdownButton}
              data-testid={ElementIds.MODEL_SELECTOR}
              variant="soft"
            >
              <Flex align="center" gapX="2">
                <BotIcon />
                <Text>{getModelShortName(model)}</Text>
              </Flex>
            </Select.Trigger>
            <Select.Content>
              <Select.Group>
                <Select.Label>Model</Select.Label>
                <ModelSelectOptions
                  currentModel={null} // No current model restriction for new tasks
                  hasAnthropicCreds={hasAnthropicCreds}
                  hasOpenAICreds={hasOpenAICreds}
                  shouldDisableOptions={true}
                  optionTestId={ElementIds.MODEL_OPTION}
                />
              </Select.Group>
            </Select.Content>
          </Select.Root>
          {sendMessageTooltipContent ? (
            <Tooltip content={sendMessageTooltipContent}>
              <Button
                variant="solid"
                onClick={handleStartTask}
                disabled={!newTaskPromptDraft || !repoInfo || isPending || defaultSystemPrompt === null}
                loading={isPending}
                className={styles.sendButton}
                data-testid={ElementIds.START_TASK_BUTTON}
              >
                Start Task
                <ArrowRightIcon />
              </Button>
            </Tooltip>
          ) : (
            <Button
              variant="solid"
              onClick={handleStartTask}
              disabled={!newTaskPromptDraft || !repoInfo || isPending}
              loading={isPending}
              className={styles.sendButton}
              data-testid={ElementIds.START_TASK_BUTTON}
            >
              Start Task
              <ArrowRightIcon />
            </Button>
          )}
        </Flex>
        {isSettingsOpen && (
          <SettingsDialog
            isOpen={isSettingsOpen}
            onOpenChange={setIsSettingsOpen}
            defaultSystemPrompt={defaultSystemPrompt ?? null}
            onSave={handleSaveDefaultPrompt}
          />
        )}
      </Flex>
      <Toast
        open={!!toast}
        onOpenChange={(open) => !open && setToast(null)}
        description={toast?.description}
        duration={5000}
        title={toast?.title}
        type={toast?.type}
      />
    </>
  );
};
