import * as Dialog from "@radix-ui/react-dialog";
import { Cross1Icon } from "@radix-ui/react-icons";
import {
  Box,
  Button,
  Flex,
  IconButton,
  Link,
  Select,
  Spinner,
  Switch,
  Text,
  Tooltip,
  VisuallyHidden,
} from "@radix-ui/themes";
import { useAtom, useAtomValue, useSetAtom } from "jotai";
import { BotIcon, ScrollTextIcon } from "lucide-react";
import { type ReactElement, useCallback, useEffect, useMemo, useState } from "react";

import { getModelCapabilities } from "~/common/modelCapabilities.ts";
import { getModelShortName } from "~/common/modelConstants.ts";
import { useModelCredentials } from "~/common/state/hooks/useModelCredentials.ts";
import { FilePreviewList } from "~/components/FilePreviewList.tsx";
import { ModelSelectOptions } from "~/components/ModelSelectOptions.tsx";

import type { LlmModel } from "../api";
import { ElementIds, forkTask, startTask, updateDefaultSystemPrompt } from "../api";
import { useImbueParams, useProjectPageParams } from "../common/NavigateUtils";
import { useModifiedEnter } from "../common/ShortcutUtils.ts";
import { taskModalMessageIDAtom, TaskModalMode, taskModalModeAtom } from "../common/state/atoms/taskModal.ts";
import {
  defaultModelAtom,
  doesSendMessageShortcutIncludeModifierAtom,
  lastUsedModelAtom,
} from "../common/state/atoms/userConfig.ts";
import {
  useForkDraftAttachedFiles,
  useNewTaskDraftAttachedFiles,
} from "../common/state/hooks/useDraftAttachedFiles.ts";
import { useProject } from "../common/state/hooks/useProjects.ts";
import { useForkPromptDraft, useNewTaskPromptDraft } from "../common/state/hooks/usePromptDraft.ts";
import { useRepoInfo } from "../common/state/hooks/useRepoInfo.ts";
import { useTaskModal } from "../common/state/hooks/useTaskModal";
import { mergeClasses, optional } from "../common/Utils";
import { BranchSelector } from "./BranchSelector.tsx";
import { Editor } from "./Editor";
import { FileUpload } from "./FileUpload.tsx";
import styles from "./TaskModal.module.scss";
import type { ToastContent } from "./Toast";
import { Toast, ToastType } from "./Toast";
import { TooltipIconButton } from "./TooltipIconButton";

export const TaskModal = (): ReactElement => {
  const { isTaskModalOpen, hideTaskModal } = useTaskModal();
  const { projectID } = useProjectPageParams();
  const project = useProject(projectID);
  const { taskID } = useImbueParams();

  const [promptDraft, setPromptDraft] = useNewTaskPromptDraft(projectID);
  const [newTaskAttachedFiles, setNewTaskAttachedFiles] = useNewTaskDraftAttachedFiles(projectID);
  const taskModalMessageID = useAtomValue(taskModalMessageIDAtom);
  const [forkPromptDraft, setForkPromptDraft] = useForkPromptDraft(taskModalMessageID);
  const [forkDraftAttachedFiles, setForkDraftAttachedFiles] = useForkDraftAttachedFiles(taskModalMessageID);
  const defaultModelPreference = useAtomValue(defaultModelAtom);
  const [model, setModel] = useState<LlmModel>(defaultModelPreference as LlmModel);
  const setLastUsedModel = useSetAtom(lastUsedModelAtom);
  const [toast, setToast] = useState<ToastContent | null>(null);

  const [taskModalMode, setTaskModalMode] = useAtom(taskModalModeAtom);

  // Get model capabilities
  const modelCapabilities = getModelCapabilities(model);

  const [localSystemPrompt, setLocalSystemPrompt] = useState<string | null | undefined>(undefined);
  const [userSelectedBranch, setUserSelectedBranch] = useState<string | null>(null);
  const [shouldCreateMore, setShouldCreateMore] = useState(false);
  const [isStartingTask, setIsStartingTask] = useState(false);
  const { hasAnthropicCreds, hasOpenAICreds, refetch: refetchCredentials } = useModelCredentials();
  const doesSendMessageShortcutIncludeModifier = useAtomValue(doesSendMessageShortcutIncludeModifierAtom);

  const { repoInfo, fetchRepoInfo } = useRepoInfo(projectID);

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
    setUserSelectedBranch(null);
  }, [projectID]);

  const defaultSystemPrompt = project?.defaultSystemPrompt;

  const isDisabled = useMemo(() => {
    if (taskModalMode === TaskModalMode.CREATE_TASK) {
      const trimmed = (promptDraft ?? "").trim();
      return (
        trimmed.length === 0 ||
        (repoInfo && repoInfo.recentBranches.length === 0) ||
        defaultSystemPrompt === undefined ||
        isStartingTask
      );
    } else if (taskModalMode === TaskModalMode.FORK_TASK) {
      const trimmed = (forkPromptDraft ?? "").trim();
      return trimmed.length === 0 || isStartingTask || taskModalMessageID === null || taskID === undefined;
    }
  }, [
    defaultSystemPrompt,
    forkPromptDraft,
    isStartingTask,
    promptDraft,
    repoInfo,
    taskID,
    taskModalMessageID,
    taskModalMode,
  ]);

  const sendMessageTooltipContent = useMemo((): string | null => {
    if (taskModalMode === TaskModalMode.CREATE_TASK && !repoInfo) return "Loading repository info...";
    if (taskModalMode === TaskModalMode.CREATE_TASK && defaultSystemPrompt === undefined) {
      return "Loading default system prompt...";
    }

    if (
      (taskModalMode == TaskModalMode.CREATE_TASK && !promptDraft) ||
      (taskModalMode === TaskModalMode.FORK_TASK && !forkPromptDraft)
    ) {
      return "Please enter a task description";
    }
    return null;
  }, [taskModalMode, repoInfo, defaultSystemPrompt, promptDraft, forkPromptDraft]);

  useEffect(() => {
    if (isTaskModalOpen) {
      fetchRepoInfo();
      refetchCredentials();
      setModel(defaultModelPreference as LlmModel);
    }
  }, [isTaskModalOpen, fetchRepoInfo, refetchCredentials, projectID, defaultModelPreference]);

  const handleSend = useCallback(async (): Promise<void> => {
    if (isDisabled) return;
    const trimmedPrompt = (promptDraft || "").trim();

    setIsStartingTask(true);

    try {
      const { data } = await startTask({
        path: { project_id: projectID },
        body: {
          prompt: trimmedPrompt,
          model,
          sourceBranch:
            sourceBranch?.indexOf(includeUncommittedLabel) === -1 ? sourceBranch : sourceBranch?.split("*")?.[0],
          isIncludingUncommittedChanges: sourceBranch?.indexOf(includeUncommittedLabel) !== -1,
          interface: "API",
          files: newTaskAttachedFiles,
        },
      });

      setIsStartingTask(false);
      const newTaskID = data.id;
      const newTaskLink = <Link href={`#/projects/${projectID}/chat/${newTaskID}`}>View Task</Link>;
      setToast({ title: "Created new task", description: newTaskLink, type: ToastType.SUCCESS });

      if (shouldCreateMore) {
        setPromptDraft("");
        setNewTaskAttachedFiles([]);
      } else {
        setPromptDraft(null);
        setNewTaskAttachedFiles([]);
        hideTaskModal();
      }
    } catch (error) {
      console.error("Failed to send message:", error);
      setToast({
        title: "",
        description: (
          <div>
            <b>Failed to send message</b>
            <br />
            <pre>{"" + error}</pre>
            <br />
            See <a href="https://github.com/imbue-ai/sculptor">help docs</a> for more information.
          </div>
        ),
        type: ToastType.ERROR,
      });
      setIsStartingTask(false);
      return;
    }
  }, [
    isDisabled,
    promptDraft,
    projectID,
    model,
    sourceBranch,
    shouldCreateMore,
    setPromptDraft,
    newTaskAttachedFiles,
    setNewTaskAttachedFiles,
    hideTaskModal,
  ]);

  const handleFork = useCallback(async (): Promise<void> => {
    if (isDisabled || taskModalMessageID === null || taskID === undefined) {
      console.error("Cannot fork task: missing required information");
      setToast({ title: "Failed to fork task", type: ToastType.ERROR });
      return;
    }
    const trimmedForkPrompt = (forkPromptDraft || "").trim();

    setIsStartingTask(true);

    try {
      const { data } = await forkTask({
        path: { project_id: projectID, task_id: taskID },
        body: {
          chatMessageId: taskModalMessageID,
          prompt: trimmedForkPrompt,
          model: model,
          files: forkDraftAttachedFiles,
        },
      });

      setIsStartingTask(false);
      const newTaskID = data.id;
      const newTaskLink = <Link href={`/projects/${projectID}/chat/${newTaskID}`}>View Task</Link>;
      setToast({ title: "Forked new task", description: newTaskLink, type: ToastType.SUCCESS });

      if (!shouldCreateMore) {
        setForkDraftAttachedFiles([]);
        hideTaskModal();
      } else {
        setForkDraftAttachedFiles([]);
      }
    } catch (error) {
      console.error("Failed to fork task:", error);
      setToast({
        title: "",
        description: (
          <div>
            <b>Failed to fork task</b>
            <br />
            <pre>{"" + error}</pre>
            <br />
            See <a href="https://github.com/imbue-ai/sculptor">help docs</a> for more information.
          </div>
        ),
        type: ToastType.ERROR,
      });
      setIsStartingTask(false);
      return;
    }
  }, [
    isDisabled,
    taskModalMessageID,
    taskID,
    forkPromptDraft,
    projectID,
    model,
    shouldCreateMore,
    forkDraftAttachedFiles,
    setForkDraftAttachedFiles,
    hideTaskModal,
  ]);

  const handleSaveSystemPrompt = useCallback(async (): Promise<void> => {
    if (localSystemPrompt === null || localSystemPrompt === undefined) return;

    try {
      const { data: updatedPrompt } = await updateDefaultSystemPrompt({
        path: { project_id: projectID },
        body: { defaultSystemPrompt: localSystemPrompt },
      });
      const promptValue = updatedPrompt ?? null;
      setLocalSystemPrompt(promptValue);
      setTaskModalMode(TaskModalMode.CREATE_TASK);
      setToast({ title: "Default system prompt updated successfully", type: ToastType.SUCCESS });
    } catch (error) {
      console.error("Failed to update default system prompt:", error);
      setToast({ title: "Failed to update default system prompt", type: ToastType.ERROR });
    }
  }, [localSystemPrompt, projectID, setTaskModalMode]);

  const onKeyDown = useModifiedEnter({
    onConfirm: useCallback(() => {
      if (taskModalMode === TaskModalMode.CREATE_TASK && !isDisabled) {
        void handleSend();
      } else if (taskModalMode === TaskModalMode.FORK_TASK && !isDisabled) {
        void handleFork();
      }
    }, [taskModalMode, isDisabled, handleSend, handleFork]),
    doesSendMessageShortcutIncludeModifier,
  });

  const focusEditor = (panelEl: HTMLElement): void => {
    const el = panelEl.querySelector(".ProseMirror") as HTMLElement | null;
    if (!el) return;
    el.focus();
    const sel = window.getSelection();
    if (!sel) return;
    const range = document.createRange();
    range.selectNodeContents(el);
    range.collapse(false);
    sel.removeAllRanges();
    sel.addRange(range);
  };

  const editorValue = useMemo((): string => {
    if (taskModalMode === TaskModalMode.EDIT_SYSTEM_PROMPT) {
      return localSystemPrompt ?? "";
    } else if (taskModalMode === TaskModalMode.CREATE_TASK) {
      return promptDraft ?? "";
    } else if (taskModalMode === TaskModalMode.FORK_TASK) {
      return forkPromptDraft ?? "";
    } else {
      throw new Error(`Unexpected task modal mode: ${taskModalMode}`);
    }
  }, [taskModalMode, localSystemPrompt, promptDraft, forkPromptDraft]);

  return (
    <>
      <Dialog.Root
        open={isTaskModalOpen}
        onOpenChange={(o) => {
          if (!o) {
            setTaskModalMode(TaskModalMode.CREATE_TASK);
            hideTaskModal();
          }
        }}
      >
        <VisuallyHidden>
          <Dialog.Title>Create new task</Dialog.Title>
        </VisuallyHidden>
        <Dialog.Content
          className={styles.modalContainer}
          aria-describedby={undefined}
          data-testid={ElementIds.TASK_MODAL}
        >
          <Flex direction="column" className={styles.body}>
            <Box
              className={styles.panel}
              tabIndex={-1}
              onMouseDown={(e) => {
                const target = e.target as HTMLElement;
                if (target.closest("[data-no-panel-focus]")) return;
                if (!target.closest(".ProseMirror")) {
                  e.preventDefault();
                  focusEditor(e.currentTarget as HTMLElement);
                }
              }}
            >
              <Box position="absolute" top="4" right="4" className={styles.close}>
                <Dialog.Close asChild>
                  <IconButton
                    variant="ghost"
                    size="1"
                    aria-label="Close"
                    data-no-panel-focus
                    data-testid={ElementIds.TASK_MODAL_CLOSE_BUTTON}
                  >
                    <Cross1Icon />
                  </IconButton>
                </Dialog.Close>
              </Box>
              <Box className={styles.panelBody} py="3" px="3">
                <Editor
                  tagName={ElementIds.TASK_MODAL_INPUT}
                  placeholder={
                    taskModalMode === TaskModalMode.EDIT_SYSTEM_PROMPT
                      ? "Enter default system prompt..."
                      : "Type a message..."
                  }
                  value={editorValue}
                  onChange={(v: string) => {
                    if (taskModalMode === TaskModalMode.EDIT_SYSTEM_PROMPT) {
                      setLocalSystemPrompt(v);
                    } else if (taskModalMode === TaskModalMode.CREATE_TASK) {
                      setPromptDraft(v);
                    } else if (taskModalMode === TaskModalMode.FORK_TASK) {
                      setForkPromptDraft(v);
                    } else {
                      throw new Error(`Unexpected task modal mode: ${taskModalMode}`);
                    }
                  }}
                  onKeyDown={onKeyDown}
                  wrapperClassName={styles.editorFill}
                  onFilesChange={(newFiles) => {
                    const setFiles =
                      taskModalMode === TaskModalMode.CREATE_TASK ? setNewTaskAttachedFiles : setForkDraftAttachedFiles;
                    setFiles((prev) => [...prev, ...newFiles]);
                  }}
                  onError={setToast}
                  footer={((): ReactElement | undefined => {
                    const files =
                      taskModalMode === TaskModalMode.CREATE_TASK ? newTaskAttachedFiles : forkDraftAttachedFiles;
                    const setFiles =
                      taskModalMode === TaskModalMode.CREATE_TASK ? setNewTaskAttachedFiles : setForkDraftAttachedFiles;
                    return files.length > 0 && setFiles ? (
                      <FilePreviewList
                        files={files}
                        onRemoveFile={(path) => setFiles((prev) => prev.filter((curr) => curr !== path))}
                      />
                    ) : undefined;
                  })()}
                />
              </Box>
            </Box>

            {taskModalMode === TaskModalMode.FORK_TASK && (
              <Flex justify="between" align="center" p="4" className={styles.footer}>
                <Flex align="center" gap="2">
                  <Text size="2" className={styles.text}>
                    Create more
                  </Text>
                  <Switch
                    checked={shouldCreateMore}
                    onCheckedChange={setShouldCreateMore}
                    aria-label="Create more"
                    data-testid={ElementIds.TASK_MODAL_CREATE_MORE_TOGGLE}
                  />
                </Flex>
                <Flex gapX="3" align="center">
                  <FileUpload
                    files={forkDraftAttachedFiles}
                    onFilesChange={setForkDraftAttachedFiles}
                    onError={setToast}
                    disabled={isStartingTask || !modelCapabilities.supportsFileAttachments}
                  />
                  <Select.Root
                    value={model}
                    onValueChange={(value: LlmModel) => {
                      setModel(value);
                      setLastUsedModel(value);
                      // Clear attached files when switching to a model that doesn't support them
                      const newCapabilities = getModelCapabilities(value);
                      if (!newCapabilities.supportsFileAttachments && forkDraftAttachedFiles.length > 0) {
                        setForkDraftAttachedFiles([]);
                      }
                    }}
                  >
                    <Select.Trigger
                      placeholder="Select model"
                      className={styles.dropdownButton}
                      data-testid={ElementIds.TASK_MODAL_MODEL_SELECTOR}
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
                          optionTestId={ElementIds.TASK_MODAL_MODEL_SELECTOR_OPTION}
                        />
                      </Select.Group>
                    </Select.Content>
                  </Select.Root>

                  {sendMessageTooltipContent ? (
                    <Tooltip content={sendMessageTooltipContent}>
                      <Button onClick={() => void handleFork()} disabled={isDisabled} className={styles.sendButton}>
                        Fork Task
                      </Button>
                    </Tooltip>
                  ) : (
                    <Button
                      onClick={() => void handleFork()}
                      disabled={isDisabled}
                      className={styles.sendButton}
                      data-testid={ElementIds.TASK_MODAL_FORK_TASK_BUTTON}
                    >
                      {isStartingTask ? (
                        <Flex align="center" gap="1">
                          <Spinner />
                          <Text size="1">Forking task...</Text>
                        </Flex>
                      ) : (
                        "Fork Task"
                      )}
                    </Button>
                  )}
                </Flex>
              </Flex>
            )}
            {taskModalMode === TaskModalMode.EDIT_SYSTEM_PROMPT && (
              <Flex justify="end" align="center" p="4" gapX="2" className={styles.footer}>
                <Button
                  onClick={() => {
                    setLocalSystemPrompt(defaultSystemPrompt);
                    setTaskModalMode(TaskModalMode.CREATE_TASK);
                  }}
                  className={styles.secondaryButton}
                  data-testid={ElementIds.TASK_MODAL_SYSTEM_PROMPT_CANCEL_BUTTON}
                >
                  Cancel
                </Button>
                <Button
                  onClick={() => void handleSaveSystemPrompt()}
                  data-testid={ElementIds.TASK_MODAL_SYSTEM_PROMPT_SAVE_BUTTON}
                >
                  Save System Prompt
                </Button>
              </Flex>
            )}
            {taskModalMode === TaskModalMode.CREATE_TASK && (
              <Flex justify="between" align="center" p="4" className={styles.footer}>
                <Flex align="center" gap="2">
                  <Text size="2" className={styles.text}>
                    Create more
                  </Text>
                  <Switch
                    checked={shouldCreateMore}
                    onCheckedChange={setShouldCreateMore}
                    aria-label="Create more"
                    data-testid={ElementIds.TASK_MODAL_CREATE_MORE_TOGGLE}
                  />
                </Flex>
                <Flex gapX="3" align="center">
                  <FileUpload
                    files={newTaskAttachedFiles}
                    onFilesChange={setNewTaskAttachedFiles}
                    onError={setToast}
                    disabled={isStartingTask || !modelCapabilities.supportsFileAttachments}
                  />
                  <TooltipIconButton
                    tooltipText={
                      !modelCapabilities.supportsSystemPrompt
                        ? "System prompts are not supported with this model"
                        : "Update system prompt"
                    }
                    variant="ghost"
                    onClick={() => {
                      setLocalSystemPrompt(defaultSystemPrompt ?? "");
                      setTaskModalMode(TaskModalMode.EDIT_SYSTEM_PROMPT);
                    }}
                    disabled={defaultSystemPrompt === undefined || !modelCapabilities.supportsSystemPrompt}
                    size="3"
                    aria-label="Toggle system prompt"
                    data-testid={ElementIds.TASK_MODAL_SYSTEM_PROMPT_OPEN_BUTTON}
                    className={mergeClasses(
                      optional(defaultSystemPrompt !== null && defaultSystemPrompt !== undefined, styles.enabled),
                      styles.systemPromptIcon,
                    )}
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
                    <Button
                      disabled={true}
                      className={styles.sendButton}
                      data-testid={ElementIds.DISABLED_BRANCH_SELECTOR}
                    >
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
                      if (!newCapabilities.supportsFileAttachments && newTaskAttachedFiles.length > 0) {
                        setNewTaskAttachedFiles([]);
                      }
                    }}
                  >
                    <Select.Trigger
                      placeholder="Select model"
                      className={styles.dropdownButton}
                      data-testid={ElementIds.TASK_MODAL_MODEL_SELECTOR}
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
                          optionTestId={ElementIds.TASK_MODAL_MODEL_SELECTOR_OPTION}
                        />
                      </Select.Group>
                    </Select.Content>
                  </Select.Root>

                  {sendMessageTooltipContent ? (
                    <Tooltip content={sendMessageTooltipContent}>
                      <Button onClick={() => void handleSend()} disabled={isDisabled} className={styles.sendButton}>
                        Start Task
                      </Button>
                    </Tooltip>
                  ) : (
                    <Button
                      onClick={() => void handleSend()}
                      disabled={isDisabled}
                      className={styles.sendButton}
                      data-testid={ElementIds.TASK_MODAL_CREATE_TASK_BUTTON}
                    >
                      {isStartingTask ? (
                        <Flex align="center" gap="1">
                          <Spinner />
                          <Text size="1">Starting task...</Text>
                        </Flex>
                      ) : (
                        "Start Task"
                      )}
                    </Button>
                  )}
                </Flex>
              </Flex>
            )}
          </Flex>
        </Dialog.Content>
      </Dialog.Root>
      <Toast
        open={!!toast}
        onOpenChange={(open) => !open && setToast(null)}
        title={toast?.title}
        description={toast?.description}
        type={toast?.type}
      />
    </>
  );
};
