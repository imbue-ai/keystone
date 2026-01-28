import { Card, Flex, IconButton, Select, Text, Tooltip } from "@radix-ui/themes";
import { useAtomValue } from "jotai";
import { ArrowRightIcon, BotIcon, ScrollTextIcon } from "lucide-react";
import type { ReactElement } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import { getModelCapabilities } from "~/common/modelCapabilities.ts";
import { getModelShortName } from "~/common/modelConstants.ts";
import { useModelCredentials } from "~/common/state/hooks/useModelCredentials.ts";
import { FilePreviewList } from "~/components/FilePreviewList.tsx";
import { ModelSelectOptions } from "~/components/ModelSelectOptions.tsx";

import type { LlmModel } from "../../../api";
import { type ChatMessage, ElementIds, sendMessage } from "../../../api";
import { CHAT_INPUT_ELEMENT_ID } from "../../../common/Constants.ts";
import { useImbueParams } from "../../../common/NavigateUtils.ts";
import { useModifiedEnter } from "../../../common/ShortcutUtils.ts";
import { globalDevModeAtom } from "../../../common/state/atoms/devMode.ts";
import {
  areSuggestionsEnabledAtom,
  doesSendMessageShortcutIncludeModifierAtom,
  isScoutBetaFeatureOnAtom,
} from "../../../common/state/atoms/userConfig.ts";
import { useDraftAttachedFiles } from "../../../common/state/hooks/useDraftAttachedFiles.ts";
import { usePromptDraft } from "../../../common/state/hooks/usePromptDraft.ts";
import { mergeClasses, optional } from "../../../common/Utils.ts";
import { Editor } from "../../../components/Editor.tsx";
import { FileUpload } from "../../../components/FileUpload.tsx";
import { Toast, type ToastContent, ToastType } from "../../../components/Toast.tsx";
import { TooltipIconButton } from "../../../components/TooltipIconButton.tsx";
import type { CheckHistory, SuggestionsData } from "../Types.ts";
import styles from "./ChatInput.module.scss";
import { ScoutPanel } from "./ScoutPanel.tsx";
import { TopSuggestions } from "./TopSuggestions.tsx";

type ChatInputProps = {
  isDisabled: boolean;
  insufficientTokens?: boolean;
  systemPrompt?: string;
  model: LlmModel;
  suggestionsData?: SuggestionsData;
  onShowSuggestions?: () => void;
  chatMessages?: Array<ChatMessage>;
  appendTextRef?: React.MutableRefObject<((text: string) => void) | null>;
  checksData?: Record<string, Record<string, CheckHistory>>;
  onFlashTab?: (tabId: string) => void;
};

export const ChatInput = ({
  isDisabled,
  insufficientTokens = false,
  systemPrompt = "",
  model,
  suggestionsData,
  onShowSuggestions,
  chatMessages,
  appendTextRef,
  checksData,
  onFlashTab,
}: ChatInputProps): ReactElement => {
  const [isSystemPromptVisible, setIsSystemPromptVisible] = useState(false);
  const systemPromptRef = useRef<HTMLTextAreaElement>(null);
  const [isChatMessage, setIsChatMessage] = useState(true);
  const [localModel, setLocalModel] = useState<LlmModel>(model);
  const [toast, setToast] = useState<ToastContent | null>(null);
  const doesSendMessageShortcutIncludeModifier = useAtomValue(doesSendMessageShortcutIncludeModifierAtom);
  const { hasAnthropicCreds, hasOpenAICreds } = useModelCredentials();
  const { projectID, taskID } = useImbueParams();

  if (!projectID) {
    throw new Error("Expected projectID to be defined");
  }

  if (!taskID) {
    throw new Error("Expected taskID to be defined");
  }

  const [promptDraft, setPromptDraft] = usePromptDraft(taskID);
  const [attachedFiles, setAttachedFiles] = useDraftAttachedFiles(taskID);

  const isDev = useAtomValue(globalDevModeAtom);
  const areSuggestionsEnabled = useAtomValue(areSuggestionsEnabledAtom);
  const isScoutBetaFeatureOn = useAtomValue(isScoutBetaFeatureOnAtom);

  const handleSend = useCallback(async (): Promise<void> => {
    if (isDisabled || !promptDraft?.trim()) {
      return;
    }

    try {
      await sendMessage({
        path: { project_id: projectID, task_id: taskID },
        body: { message: promptDraft, model: localModel, files: attachedFiles },
      });
      setPromptDraft(null);
      setAttachedFiles([]);
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
    }
  }, [isDisabled, promptDraft, projectID, taskID, localModel, attachedFiles, setPromptDraft, setAttachedFiles]);

  const handleKeyPress = useModifiedEnter({
    onConfirm: handleSend,
    doesSendMessageShortcutIncludeModifier,
  });

  useEffect(() => {
    if (isSystemPromptVisible && systemPromptRef.current) {
      systemPromptRef.current.focus();
    }
  }, [isSystemPromptVisible]);

  useEffect(() => {
    if (!appendTextRef) {
      return;
    }

    appendTextRef.current = (text: string): void => {
      const currentDraft = promptDraft || "";
      setPromptDraft(currentDraft ? `${currentDraft}\n${text}\n` : `${text}\n`);
    };
  }, [appendTextRef, setPromptDraft, promptDraft]);

  // Update localModel when the model prop changes (e.g., when switching between tasks)
  useEffect(() => {
    setLocalModel(model);
  }, [model]);

  // Get model capabilities
  const modelCapabilities = getModelCapabilities(localModel);

  return (
    <>
      {isScoutBetaFeatureOn && <ScoutPanel />}
      {areSuggestionsEnabled && (
        <TopSuggestions
          suggestionsData={suggestionsData}
          onShowSuggestions={onShowSuggestions}
          chatMessages={chatMessages}
          appendTextRef={appendTextRef}
          checksData={checksData}
          onFlashTab={onFlashTab}
        />
      )}
      <div className={styles.container} id={CHAT_INPUT_ELEMENT_ID}>
        <Card className={`${styles.systemPromptCard} ${isSystemPromptVisible ? styles.visible : ""}`}>
          <div className={styles.systemPromptHeader}>
            <span className={styles.systemPromptLabel}>
              SYSTEM PROMPT <span className={styles.readOnlyLabel}>READ ONLY</span>
            </span>
          </div>
          <textarea
            ref={systemPromptRef}
            className={styles.systemPromptTextarea}
            value={systemPrompt}
            placeholder="No system prompt was set for this task."
            readOnly
            rows={8}
            data-testid={ElementIds.CHAT_PANEL_SYSTEM_PROMPT_TEXT}
          />
        </Card>

        <Flex direction="column" gapY="3" className={styles.inputSection}>
          {isChatMessage ? (
            <Editor
              placeholder="Type a message..."
              value={promptDraft || ""}
              onChange={(newValue: string) => setPromptDraft(newValue)}
              onKeyDown={handleKeyPress}
              tagName="CHAT_INPUT"
              onFilesChange={(newFiles) => setAttachedFiles((prev) => [...prev, ...newFiles])}
              onError={setToast}
              taskID={taskID}
              key={`chat-input-${taskID}`} // Reset editor state when switching tasks (e.g. slash command cache).
              footer={
                attachedFiles.length > 0 ? (
                  <FilePreviewList
                    files={attachedFiles}
                    onRemoveFile={(path) => setAttachedFiles((prev) => prev.filter((curr) => curr !== path))}
                  />
                ) : undefined
              }
            />
          ) : (
            <Editor
              placeholder="Type a command..."
              value={promptDraft || ""}
              onChange={(newValue: string) => setPromptDraft(newValue)}
              onKeyDown={handleKeyPress}
              tagName="TERMINAL_INPUT"
            />
          )}
          <Flex align="center" justify="between" gapX="4" direction="row" className={styles.actionButtons}>
            {isDev ? (
              <Select.Root
                value={isChatMessage ? "chat" : "terminal"}
                onValueChange={(value) => setIsChatMessage(value === "chat")}
              >
                <Select.Trigger className={styles.modelSelector} variant="soft">
                  <Flex align="center" gapX="2">
                    <Text size="2">{isChatMessage ? "Chat" : "Terminal"}</Text>
                  </Flex>
                </Select.Trigger>
                <Select.Content>
                  <Select.Group>
                    <Select.Label>Input mode</Select.Label>
                    <Select.Item value="chat">Chat</Select.Item>
                    <Select.Item value="terminal">Terminal</Select.Item>
                  </Select.Group>
                </Select.Content>
              </Select.Root>
            ) : (
              <div> </div>
            )}
            <Flex align="center" gapX="3">
              {isChatMessage && (
                <FileUpload
                  files={attachedFiles}
                  onFilesChange={setAttachedFiles}
                  onError={setToast}
                  disabled={isDisabled || !modelCapabilities.supportsFileAttachments}
                />
              )}
              {isChatMessage && (
                <TooltipIconButton
                  tooltipText={
                    !modelCapabilities.supportsSystemPrompt
                      ? "System prompts are not supported with this model"
                      : "System prompt"
                  }
                  variant="ghost"
                  size="3"
                  onClick={() => setIsSystemPromptVisible(!isSystemPromptVisible)}
                  aria-label="Update system prompt"
                  className={mergeClasses(optional(systemPrompt.length > 0, styles.enabled), styles.systemPromptIcon)}
                  data-testid={ElementIds.CHAT_PANEL_SYSTEM_PROMPT_OPEN_BUTTON}
                  disabled={!modelCapabilities.supportsSystemPrompt}
                >
                  <ScrollTextIcon />
                </TooltipIconButton>
              )}
              {isChatMessage && (
                <Select.Root value={localModel} onValueChange={(value: LlmModel) => setLocalModel(value)}>
                  <Select.Trigger
                    variant="soft"
                    className={styles.modelSelector}
                    data-testid={ElementIds.MODEL_SELECTOR}
                  >
                    <Flex align="center" gapX="2">
                      <BotIcon size={16} />
                      <Text size="2">{getModelShortName(localModel)}</Text>
                    </Flex>
                  </Select.Trigger>
                  <Select.Content>
                    <ModelSelectOptions
                      currentModel={localModel}
                      hasAnthropicCreds={hasAnthropicCreds}
                      hasOpenAICreds={hasOpenAICreds}
                      shouldDisableOptions={false}
                      optionTestId={ElementIds.MODEL_OPTION}
                    />
                  </Select.Content>
                </Select.Root>
              )}
              {insufficientTokens ? (
                <Tooltip content="Please compact context first">
                  <IconButton
                    disabled={isDisabled}
                    onClick={handleSend}
                    className={styles.sendButton}
                    aria-label="Send message"
                    data-testid={ElementIds.SEND_BUTTON}
                  >
                    <ArrowRightIcon size={16} />
                  </IconButton>
                </Tooltip>
              ) : (
                <IconButton
                  disabled={isDisabled}
                  onClick={handleSend}
                  className={styles.sendButton}
                  aria-label="Send message"
                  data-testid={ElementIds.SEND_BUTTON}
                >
                  <ArrowRightIcon size={16} />
                </IconButton>
              )}
            </Flex>
          </Flex>
        </Flex>
      </div>
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
