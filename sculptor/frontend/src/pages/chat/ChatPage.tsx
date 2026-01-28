import { Box, Flex, ScrollArea, Tabs } from "@radix-ui/themes";
import { useAtom, useAtomValue, useSetAtom } from "jotai";
import type { ReactElement } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";

import { ArtifactType } from "../../api";
import { CHAT_PAGE_ID } from "../../common/Constants.ts";
import { useComponentWidth } from "../../common/Hooks";
import { useTaskPageParams } from "../../common/NavigateUtils.ts";
import { isRightPanelOpenAtom } from "../../common/state/atoms/sidebar";
import {
  flashTabIdAtomFamily,
  isNarrowViewingChatAtomFamily,
  selectedArtifactIdAtomFamily,
} from "../../common/state/atoms/tasks";
import { hasSeenPairingModeModalAtom } from "../../common/state/atoms/userConfig.ts";
import { useArtifactViewsByTabOrder } from "../../common/state/hooks/useArtifactViews";
import { useIsNarrowLayout } from "../../common/state/hooks/useComponentWidthById.ts";
import { useInstructionalModal } from "../../common/state/hooks/useInstructionalModal.ts";
import { useTaskChatMessages, useTaskDetailWithDefaults } from "../../common/state/hooks/useTaskDetail";
import { useTask } from "../../common/state/hooks/useTaskHelpers";
import styles from "./ChatPage.module.scss";
import { extractUserMessageIds, filterSuggestionsFromCheckOutputs } from "./components/artifacts/suggestionUtils";
import { ArtifactsPanel } from "./components/ArtifactsPanel.tsx";
import { BottomBar } from "./components/BottomBar";
import { ChatInterface } from "./components/ChatInterface";
import { Header } from "./components/Header.tsx";
import { useArtifactSync } from "./hooks/useArtifactSync";
import { useChatSmoothStreaming } from "./hooks/useSmoothStreaming.ts";

export const ChatPage = (): ReactElement => {
  const { projectID, taskID } = useTaskPageParams();
  const [selectedArtifactTab, setSelectedArtifactTab] = useState<string>("");
  const appendTextRef = useRef<((text: string) => void) | null>(null);
  const { showInstructionalModal } = useInstructionalModal();
  const shouldShowPairingModeModal = useAtomValue(hasSeenPairingModeModalAtom) === false;

  if (!projectID) {
    throw new Error("Expected projectID to be defined");
  }

  if (!taskID) {
    throw new Error("Expected taskID to be defined");
  }
  const task = useTask(taskID);

  // Get task detail state from global atoms
  const { artifacts, checksData, checksDefinedForMessage, feedbackByMessageId } = useTaskDetailWithDefaults(taskID);

  const { chatMessages, inProgressChatMessage, workingUserMessageId, queuedChatMessages } = useTaskChatMessages(taskID);
  const smoothInProgressChatMessage = useChatSmoothStreaming(inProgressChatMessage);
  const smoothChatMessages = useMemo(() => {
    if (smoothInProgressChatMessage) {
      return [...chatMessages.slice(0, -1), smoothInProgressChatMessage];
    }
    return chatMessages;
  }, [chatMessages, smoothInProgressChatMessage]);

  // Sync artifacts for the currently viewed task only
  useArtifactSync(projectID, taskID);

  // On load, show the pairing mode modal if and only if the user hasn't seen it before
  useEffect(() => {
    if (shouldShowPairingModeModal) {
      showInstructionalModal();
    }
  }, [shouldShowPairingModeModal, showInstructionalModal]);

  const { ref: containerRef } = useComponentWidth(CHAT_PAGE_ID);
  const [isRightPanelOpen] = useAtom(isRightPanelOpenAtom);
  const isNarrowLayout = useIsNarrowLayout();
  const isSinglePanelLayout = isNarrowLayout || !isRightPanelOpen;

  const enabledArtifactViews = useArtifactViewsByTabOrder();
  const firstArtifactViewId = enabledArtifactViews.length > 0 ? enabledArtifactViews[0].id : "";
  // Track the currently selected artifact (shared between layouts)
  const [selectedArtifactId, setSelectedArtifactId] = useAtom(selectedArtifactIdAtomFamily(taskID));
  // Track whether we're viewing chat in narrow mode
  const [isNarrowViewingChat, setIsNarrowViewingChat] = useAtom(isNarrowViewingChatAtomFamily(taskID));
  const setFlashTabId = useSetAtom(flashTabIdAtomFamily(taskID));

  const diffArtifact = artifacts[ArtifactType.DIFF];
  const usageData = artifacts[ArtifactType.USAGE];
  const suggestionsData = filterSuggestionsFromCheckOutputs(artifacts[ArtifactType.NEW_CHECK_OUTPUTS]);
  const userMessageIds = extractUserMessageIds(smoothChatMessages);

  const handleLogsClick = (): void => {
    setSelectedArtifactId("Logs");
    if (isSinglePanelLayout) {
      setIsNarrowViewingChat(false);
    }
  };

  const handleNarrowTabChange = (viewId: string): void => {
    if (viewId === "chat") {
      setIsNarrowViewingChat(true);
    } else {
      setSelectedArtifactId(viewId);
      setIsNarrowViewingChat(false);
    }
  };

  const handleFlashTab = (tabId: string): void => {
    setFlashTabId(tabId);
  };

  useEffect(() => {
    if (firstArtifactViewId && !selectedArtifactId) {
      setSelectedArtifactId(firstArtifactViewId);
    }
  }, [firstArtifactViewId, selectedArtifactId, setSelectedArtifactId]);

  useEffect(() => {
    if (isSinglePanelLayout) {
      setIsNarrowViewingChat(true);
    }
  }, [isSinglePanelLayout, setIsNarrowViewingChat]);

  const narrowSelectedTab = isNarrowViewingChat ? "chat" : selectedArtifactId;

  return (
    <Flex direction="column" className={styles.container} overflowY="hidden" ref={containerRef}>
      <Header diffArtifact={diffArtifact} />
      {isSinglePanelLayout ? (
        <Tabs.Root
          value={narrowSelectedTab}
          onValueChange={handleNarrowTabChange}
          className={styles.narrowLayoutContent}
        >
          <Tabs.List className={styles.narrowTabsList}>
            <Tabs.Trigger value="chat">Chat</Tabs.Trigger>
            {enabledArtifactViews.map((artifactView) => (
              <Tabs.Trigger key={artifactView.id} value={artifactView.id}>
                <artifactView.tabLabelComponent artifacts={artifacts} />
              </Tabs.Trigger>
            ))}
          </Tabs.List>

          <Tabs.Content value="chat" className={styles.narrowTabContent}>
            <Flex direction="column" height="100%" minHeight="0">
              <Flex flexGrow="1" minHeight="0" justify="center">
                <ChatInterface
                  chatMessages={smoothChatMessages}
                  isStreaming={smoothInProgressChatMessage !== null}
                  workingUserMessageId={workingUserMessageId}
                  queuedChatMessages={queuedChatMessages}
                  tokensUsed={usageData?.tokenInfo || 0}
                  onClickLogsWhileBuilding={handleLogsClick}
                  checksData={checksData}
                  checksDefinedForMessage={checksDefinedForMessage}
                  suggestionsData={suggestionsData}
                  onShowSuggestions={() => {
                    setSelectedArtifactId("Suggestions");
                    setIsNarrowViewingChat(false);
                  }}
                  onFlashTab={handleFlashTab}
                  onShowChecks={() => {
                    setSelectedArtifactId("Checks");
                    setIsNarrowViewingChat(false);
                  }}
                  appendTextRef={appendTextRef}
                  feedbackByMessageId={feedbackByMessageId}
                />
              </Flex>
              <Box flexShrink="0" height="32px">
                <BottomBar tokensUsed={usageData?.tokenInfo} />
              </Box>
            </Flex>
          </Tabs.Content>

          {enabledArtifactViews.map((artifactView) => (
            <Tabs.Content key={artifactView.id} value={artifactView.id} className={styles.narrowTabContent}>
              {task && (
                <ScrollArea className={styles.scrollArea}>
                  <Box
                    p="4"
                    width="calc(min(80%, var(--main-content-width)))"
                    minWidth="0"
                    height="100%"
                    className={styles.innerBox}
                  >
                    {/*FIXME: we shouldn't need to pass the task in here. All the data should be through the artifact.*/}
                    {/* This is here bc of the terminal artifact, to get the ttyd url. We should just make an artifact for that instead */}
                    {/* TODO(andrew.laack): Refactor prop drilling of userMessageIds - consider using Jotai atoms or custom hooks to eliminate prop drilling */}
                    <artifactView.contentComponent
                      artifacts={artifacts}
                      checksData={checksData}
                      task={task}
                      userMessageIds={userMessageIds}
                      appendTextRef={appendTextRef}
                    />
                  </Box>
                </ScrollArea>
              )}
            </Tabs.Content>
          ))}
        </Tabs.Root>
      ) : (
        <PanelGroup direction="horizontal" className={styles.panelGroup}>
          <Panel defaultSize={60} minSize={30}>
            <ChatInterface
              chatMessages={smoothChatMessages}
              isStreaming={smoothInProgressChatMessage !== null}
              workingUserMessageId={workingUserMessageId}
              queuedChatMessages={queuedChatMessages}
              tokensUsed={usageData?.tokenInfo || 0}
              onClickLogsWhileBuilding={() => setSelectedArtifactTab("Logs")}
              checksData={checksData}
              checksDefinedForMessage={checksDefinedForMessage}
              suggestionsData={suggestionsData}
              onShowSuggestions={() => setSelectedArtifactTab("Suggestions")}
              onShowChecks={() => setSelectedArtifactTab("Checks")}
              appendTextRef={appendTextRef}
              feedbackByMessageId={feedbackByMessageId}
              onFlashTab={handleFlashTab}
            />
          </Panel>
          <PanelResizeHandle className={styles.resizeHandle} />
          <Panel defaultSize={40} minSize={20}>
            {/* TODO(andrew.laack): Refactor prop drilling of userMessageIds - consider using Jotai atoms or custom hooks to eliminate prop drilling */}
            <ArtifactsPanel
              artifacts={artifacts}
              checksData={checksData}
              selectedTab={selectedArtifactTab}
              onTabChange={setSelectedArtifactTab}
              userMessageIds={userMessageIds}
              appendTextRef={appendTextRef}
              checksDefinedForMessage={checksDefinedForMessage}
            />
          </Panel>
        </PanelGroup>
      )}
      {!isSinglePanelLayout && <BottomBar tokensUsed={usageData?.tokenInfo} />}
    </Flex>
  );
};
