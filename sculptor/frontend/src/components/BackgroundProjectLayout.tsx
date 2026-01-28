/** This dummy layout was created with Sculptor to represent an "in-progress" main screen.
 *
 * While we have a moderate amount of reuse in this component, there is a lot of duplication with the actual layout. Please be cautious about keeping this up to date.
 */

import { Badge, Box, Button, Flex, ScrollArea, Tabs, Text, Tooltip } from "@radix-ui/themes";
import { Card, IconButton } from "@radix-ui/themes";
import {
  ArchiveIcon,
  Check,
  CheckSquare,
  FileText,
  GitBranch,
  HomeIcon,
  PanelLeftIcon,
  PanelRightIcon,
  PlusIcon,
  SearchIcon,
  Server,
  SettingsIcon,
  Terminal,
} from "lucide-react";
import { ArrowUpDownIcon } from "lucide-react";
import { ArrowRightIcon, BotIcon, Loader2, ScrollTextIcon } from "lucide-react";
import type { ReactElement } from "react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";

import { RenderTaskItem } from "~/components/TaskItem";

import { TaskStatus } from "../api";
import { getMetaKey, getTitleBarLeftPadding, TITLEBAR_HEIGHT } from "../electron/utils.ts";
import styles from "../layouts/ProjectLayout.module.scss";
import todoListStyles from "../pages/chat/components/artifacts/TodoListArtifactView.module.scss";
import chatInputStyles from "../pages/chat/components/ChatInput.module.scss";
import chatInterfaceStyles from "../pages/chat/components/ChatInterface.module.scss";
import syncButtonStyles from "../pages/chat/components/SyncButton.module.scss";
import { TooltipIconButton } from "./TooltipIconButton.tsx";

const DummySidebar = (): ReactElement => {
  const metaKey = getMetaKey();

  return (
    <Flex direction="column" justify="between" pb="3" height="100%" style={{ backgroundColor: "var(--gold-2)" }}>
      <Flex direction="column" flexGrow="1" overflow="hidden">
        {/* Titlebar row */}
        <Flex
          justify="between"
          align="center"
          pl={getTitleBarLeftPadding(false)}
          pr="10px"
          height={`${TITLEBAR_HEIGHT + 4}px`}
          flexShrink="0"
        >
          <Flex>
            <TooltipIconButton tooltipText="">
              <PanelLeftIcon width={16} height={16} />
            </TooltipIconButton>
          </Flex>
          <Flex>
            <TooltipIconButton tooltipText="">
              <SearchIcon width={16} height={16} />
            </TooltipIconButton>
            <TooltipIconButton tooltipText="">
              <HomeIcon width={16} height={16} />
            </TooltipIconButton>
          </Flex>
        </Flex>

        {/* New Agent Button */}
        <Flex align="center" width="100%" gapX="3" mb="3" px="4">
          <Box flexGrow="1" px="9px" mt="2">
            <div
              style={{
                width: "100%",
                color: "var(--gold-9)",
                fontWeight: 500,
                padding: "8px 12px",
                borderRadius: "6px",
                backgroundColor: "transparent",
              }}
            >
              <Flex justify="between" align="center" width="100%">
                <Flex align="center" gapX="2">
                  <PlusIcon size={16} />
                  <Text>New Agent</Text>
                </Flex>
                <Text>{metaKey}+N</Text>
              </Flex>
            </div>
          </Box>
        </Flex>

        {/* Task list area */}
        <ScrollArea style={{ flex: 1 }}>
          <Flex direction="column" px="4">
            <RenderTaskItem
              task={{
                id: "1",
                title: "Add welcome modal overlay",
                initialPrompt: "",
                status: TaskStatus.READY,
                branchName: "sculptor/welcome-modal",
              }}
              relativeTime="2 minutes ago"
              projectId="project-1"
              isSelected={true}
              isArchived={false}
              isMenuOpen={false}
            />

            <RenderTaskItem
              task={{
                id: "2",
                title: "Add Linux build support",
                initialPrompt: "",
                status: TaskStatus.READY,
                branchName: "sculptor/build-on-linux",
              }}
              relativeTime="1 hour ago"
              projectId="sculptor/project-1"
              isSelected={false}
              isArchived={false}
              isMenuOpen={false}
            />

            <RenderTaskItem
              task={{
                id: "3",
                title: "Fix Docker integration bug",
                initialPrompt: "",
                status: TaskStatus.BUILDING,
                branchName: "sculptor/fix-docker-bug",
              }}
              relativeTime="30 minutes ago"
              projectId="project-1"
              isSelected={false}
              isArchived={false}
              isMenuOpen={false}
            />

            <RenderTaskItem
              task={{
                id: "4",
                title: "Improve AI response times",
                initialPrompt: "",
                status: TaskStatus.READY,
                branchName: "sculptor/improve-response-times",
              }}
              relativeTime="2 days ago"
              projectId="project-1"
              isSelected={false}
              isArchived={false}
              isMenuOpen={false}
            />
          </Flex>
        </ScrollArea>
      </Flex>

      {/* Bottom controls */}
      <Flex direction="column" gapY="3" px="4" pt="3">
        <Flex direction="row" justify="between" align="center" px="1">
          <TooltipIconButton tooltipText="">
            <ArchiveIcon width={16} height={16} />
          </TooltipIconButton>
          <Flex gapX="3">
            <TooltipIconButton tooltipText="">
              <SettingsIcon width={16} height={16} />
            </TooltipIconButton>
            <TooltipIconButton tooltipText="">
              <Text>?</Text>
            </TooltipIconButton>
          </Flex>
        </Flex>

        {/* Project Selector */}
        <Box
          p="2"
          style={{
            backgroundColor: "var(--gold-3)",
            borderRadius: "6px",
            border: "1px solid var(--gold-5)",
          }}
        >
          <Text size="2" color="gray">
            Your first project
          </Text>
        </Box>
      </Flex>
    </Flex>
  );
};

const DummyChatHeader = (): ReactElement => {
  return (
    <Flex
      align="center"
      gap="3"
      py="2"
      pr="3"
      pl={getTitleBarLeftPadding(true)}
      justify="between"
      style={{
        backgroundColor: "var(--gray-2)",
        borderBottom: "1px solid var(--gray-5)",
        minHeight: "52px",
      }}
    >
      <Flex align="center" gap="3" minWidth="0" flexGrow="1">
        <Text truncate style={{ fontSize: "14px", fontWeight: 500 }}>
          Your first Sculptor project
        </Text>
        <Badge color="gold" style={{ fontSize: "12px" }}>
          sculptor/code-with-us
        </Badge>
      </Flex>
      <Flex gap="4" align="center" flexShrink="0">
        <TooltipIconButton tooltipText="">
          <Server size={16} color="var(--green-9)" />
        </TooltipIconButton>
        <Flex gap="1">
          <Flex className={syncButtonStyles.syncButtonContainer} data-real-sync-state="INACTIVE">
            <Tooltip content="Start Pairing Mode">
              <Button
                variant="soft"
                size="1"
                className={syncButtonStyles.mainButton}
                data-visual-state="INACTIVE"
                data-variant="soft"
                style={{ cursor: "default", justifyContent: "flex-start" }}
              >
                <ArrowUpDownIcon />
              </Button>
            </Tooltip>
          </Flex>
        </Flex>
        <TooltipIconButton tooltipText="">
          <PanelRightIcon width={16} height={16} />
        </TooltipIconButton>
      </Flex>
    </Flex>
  );
};

const DummyRightPanel = (): ReactElement => {
  return (
    <Flex direction="column" height="100%" style={{ backgroundColor: "var(--gray-1)" }}>
      <Tabs.Root value="Plan" className="h-full">
        <Tabs.List
          style={{
            padding: "8px",
            backgroundColor: "var(--gray-2)",
            borderBottom: "1px solid var(--gray-5)",
          }}
        >
          <Tabs.Trigger value="Plan">
            <CheckSquare size={14} />
            Plan
          </Tabs.Trigger>
          <Tabs.Trigger value="Changes">
            <GitBranch size={14} />
            Changes
          </Tabs.Trigger>
          <Tabs.Trigger value="Terminal">
            <Terminal size={14} />
            Terminal
          </Tabs.Trigger>
          <Tabs.Trigger value="Logs">
            <FileText size={14} />
            Logs
          </Tabs.Trigger>
        </Tabs.List>

        <Tabs.Content value="Plan" style={{ flex: 1, padding: "16px" }}>
          <Flex direction="column" className={todoListStyles.todoListContainer} gapY="3">
            <Flex direction="column" gapY="3" className={todoListStyles.todoList}>
              {/* Completed todo item */}
              <Flex align="center" gap="3" pl="8px" className={todoListStyles.todoItem}>
                <Box className={todoListStyles.statusIndicator}>
                  <Check className={todoListStyles.checkIcon} />
                </Box>
                <Text size="2" className={`${todoListStyles.todoText} ${todoListStyles.completed}`} truncate={true}>
                  Download and Install Sculptor
                </Text>
                <Badge color="gold" variant="soft" size="1" className={todoListStyles.priorityBadge}>
                  P1
                </Badge>
              </Flex>

              <Flex
                align="center"
                gap="3"
                pl="8px"
                className={`${todoListStyles.todoItem} ${todoListStyles.inProgress}`}
              >
                <Box className={todoListStyles.statusIndicator}>
                  <Loader2 size="1" />
                </Box>
                <Text size="2" className={todoListStyles.todoText} truncate={true}>
                  Complete onboarding and setup
                </Text>
                <Badge color="gold" variant="soft" size="1" className={todoListStyles.priorityBadge}>
                  P1
                </Badge>
              </Flex>

              {/* Pending todo item */}
              <Flex align="center" gap="3" pl="8px" className={todoListStyles.todoItem}>
                <Box className={todoListStyles.statusIndicator}>
                  <Text size="2" color="gray">
                    3.
                  </Text>
                </Box>
                <Text size="2" className={todoListStyles.todoText} truncate={true}>
                  Start coding
                </Text>
                <Badge color="gold" variant="soft" size="1" className={todoListStyles.priorityBadge}>
                  P1
                </Badge>
              </Flex>

              {/* Another pending todo item */}
              <Flex align="center" gap="3" pl="8px" className={todoListStyles.todoItem}>
                <Box className={todoListStyles.statusIndicator}>
                  <Text size="2" color="gray">
                    4.
                  </Text>
                </Box>
                <Text size="2" className={todoListStyles.todoText} truncate={true}>
                  Have fun!
                </Text>
                <Badge color="gold" variant="soft" size="1" className={todoListStyles.priorityBadge}>
                  P0
                </Badge>
              </Flex>
            </Flex>

            {/* Progress indicator */}
            <Flex align="center" gap="2" className={todoListStyles.progressIndicator} p="3">
              <Loader2 size="1" />
              <Text size="2" color="gray">
                1 of 4 Done
              </Text>
              <Text size="2" color="gray" className={todoListStyles.ghostText}>
                Working on Todo #2
              </Text>
            </Flex>
          </Flex>
        </Tabs.Content>

        <Tabs.Content value="Changes" style={{ flex: 1, padding: "16px" }}>
          <Flex direction="column" gap="3">
            <Text size="3" weight="bold">
              File Changes
            </Text>
            <Box
              p="3"
              style={{
                backgroundColor: "var(--gray-3)",
                borderRadius: "6px",
                border: "1px solid var(--gray-5)",
              }}
            >
              <Text size="2" color="gray">
                No changes yet
              </Text>
            </Box>
          </Flex>
        </Tabs.Content>
      </Tabs.Root>
    </Flex>
  );
};

const DummyChatInterface = (): ReactElement => {
  return (
    <Flex
      direction="column"
      className={chatInterfaceStyles.mainContent}
      pb="4"
      align="center"
      justify="center"
      width="100%"
      position="relative"
    >
      <ScrollArea className={chatInterfaceStyles.messageArea}>
        <div className={chatInterfaceStyles.spacer} />
        <Flex direction="column" maxWidth="100%" className={chatInterfaceStyles.messageContainer}>
          <Box
            p="3"
            mb="3"
            style={{
              borderRadius: "8px",
              marginLeft: "auto",
              maxWidth: "80%",
            }}
          >
            <Text size="2" color="gray">
              Welcome! Start chatting with your AI agent...
            </Text>
          </Box>

          <Box
            p="4"
            mb="4"
            style={{
              backgroundColor: "var(--gray-2)",
              borderRadius: "8px",
              border: "1px solid var(--gray-4)",
            }}
          >
            <Text size="2">
              Please make the welcome page look more attractive by rendering a representative window of Sculptor in the
              background, below a modal and a translucent overlay.
            </Text>
          </Box>

          <Box
            p="3"
            mb="3"
            style={{
              borderRadius: "8px",
              marginLeft: "auto",
              maxWidth: "80%",
            }}
          >
            <Text size="2" color="gray">
              I&apos;ll help you modify the Welcome component to display as a modal overlay over the ProjectLayout. Let
              me start by examining the current code structure.
            </Text>
          </Box>

          <div style={{ minHeight: "64px" }} />
        </Flex>
      </ScrollArea>

      {/* Chat Input */}
      <div className={chatInputStyles.container}>
        <Card className={chatInputStyles.chatInputCard}>
          <Flex direction="column" gapY="3" className={chatInputStyles.inputSection}>
            {/* Message Input Area */}
            <Box
              p="3"
              style={{
                backgroundColor: "var(--gray-1)",
                borderRadius: "8px",
                border: "1px solid var(--gray-5)",
                minHeight: "60px",
                display: "flex",
                alignItems: "center",
              }}
            >
              <Text size="2" color="gray">
                Type a message...
              </Text>
            </Box>

            {/* Action Buttons */}
            <Flex align="center" justify="between" gapX="4" direction="row" className={chatInputStyles.actionButtons}>
              <div> </div>
              <Flex align="center" gapX="2">
                <Tooltip content="Update system prompt">
                  <IconButton variant="ghost" size="3" className={chatInputStyles.systemPromptIcon}>
                    <ScrollTextIcon />
                  </IconButton>
                </Tooltip>
                <Button variant="soft" className={chatInputStyles.modelSelector}>
                  <Flex align="center" gapX="2">
                    <BotIcon size={16} />
                    <Text size="2">Sonnet</Text>
                  </Flex>
                </Button>
                <IconButton className={chatInputStyles.sendButton} style={{ cursor: "default" }}>
                  <ArrowRightIcon size={16} />
                </IconButton>
              </Flex>
            </Flex>
          </Flex>
        </Card>
      </div>
    </Flex>
  );
};

const DummyMainContent = (): ReactElement => {
  return (
    <Flex direction="column" height="100%" width="100%">
      <DummyChatHeader />

      {/* Chat content with right panel */}
      <PanelGroup direction="horizontal" style={{ flex: 1 }}>
        <Panel defaultSize={60} minSize={30}>
          <DummyChatInterface />
        </Panel>
        <PanelResizeHandle
          style={{
            width: "1px",
            backgroundColor: "var(--gray-5)",
          }}
        />
        <Panel defaultSize={40} minSize={20}>
          <DummyRightPanel />
        </Panel>
      </PanelGroup>
    </Flex>
  );
};

export const BackgroundProjectLayout = (): ReactElement => {
  return (
    <Flex direction="column" height="100vh" width="100vw" position="relative">
      <PanelGroup direction="horizontal" className={styles.panelGroup}>
        <Panel id="sidebar" defaultSize={22} maxSize={50} order={1} style={{ minWidth: "320px" }}>
          <DummySidebar />
        </Panel>
        <PanelResizeHandle className={styles.resizeHandle} />
        <Panel id="main" order={2}>
          <DummyMainContent />
        </Panel>
      </PanelGroup>
    </Flex>
  );
};
