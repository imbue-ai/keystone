import { Box, Flex, ScrollArea, Select, Spinner, Tabs } from "@radix-ui/themes";
import { useAtom } from "jotai";
import type { ReactElement } from "react";
import { useEffect, useState } from "react";

import { ElementIds } from "../../../api";
import {
  ARTIFACTS_PANEL_DROPDOWN_BREAKPOINT,
  ARTIFACTS_PANEL_ICON_ONLY_BREAKPOINT,
} from "../../../common/Constants.ts";
import { useComponentWidth } from "../../../common/Hooks";
import { useImbueParams } from "../../../common/NavigateUtils";
import { flashTabIdAtomFamily } from "../../../common/state/atoms/tasks";
import { useArtifactViewsByTabOrder } from "../../../common/state/hooks/useArtifactViews";
import { useTask } from "../../../common/state/hooks/useTaskHelpers";
import type { ArtifactsMap, CheckHistory } from "../Types.ts";
import styles from "./ArtifactsPanel.module.scss";

type ArtifactsPanelProps = {
  artifacts: ArtifactsMap;
  checksData?: Record<string, Record<string, CheckHistory>>;
  selectedTab?: string;
  onTabChange?: (tabId: string) => void;
  userMessageIds?: Array<string>;
  appendTextRef?: React.MutableRefObject<((text: string) => void) | null>;
  checksDefinedForMessage?: Set<string>;
};

export const ArtifactsPanel = ({
  artifacts,
  checksData,
  selectedTab,
  onTabChange,
  userMessageIds,
  appendTextRef,
  checksDefinedForMessage,
}: ArtifactsPanelProps): ReactElement => {
  const { taskID } = useImbueParams();
  if (!taskID) {
    throw new Error("Task ID is required to render the ArtifactsPanel.");
  }
  const [flashTabId, setFlashTabId] = useAtom(flashTabIdAtomFamily(taskID));
  const { ref: panelRef, width: panelWidth } = useComponentWidth();
  const shouldShowDropdown = panelWidth > 0 && panelWidth < ARTIFACTS_PANEL_DROPDOWN_BREAKPOINT;
  const shouldHideIcons = panelWidth > 0 && panelWidth < ARTIFACTS_PANEL_ICON_ONLY_BREAKPOINT && !shouldShowDropdown;

  const enabledArtifactViews = useArtifactViewsByTabOrder();
  const task = useTask(taskID);

  const [flashingTabId, setFlashingTabId] = useState<string | null>(null);

  useEffect(() => {
    if (flashTabId) {
      setFlashingTabId(flashTabId);
      setFlashTabId(null);
      const timer = setTimeout(() => {
        setFlashingTabId(null);
      }, 500);
      return (): void => clearTimeout(timer);
    }
  }, [flashTabId, setFlashTabId]);

  const fallbackViewId = enabledArtifactViews.length > 0 ? enabledArtifactViews[0].id : "";
  const selectedView = enabledArtifactViews.find((view) => view.id === selectedTab);
  const activeView = selectedView ?? enabledArtifactViews.find((view) => view.id === fallbackViewId);
  const selectedPluginId = activeView ? activeView.id : "";

  // TODO: Could we do a smarter loading state that doesn't block the whole panel? Like maybe, more engaging skeletons per tab?
  if (!task) {
    return (
      <Flex height="100%" justify="center" align="center">
        <Spinner size="3" />
      </Flex>
    );
  }

  return (
    <Flex
      direction="column"
      height="100%"
      className={styles.artifactsPanel}
      data-testid={ElementIds.ARTIFACT_PANEL}
      ref={panelRef}
    >
      {shouldShowDropdown ? (
        // Dropdown header for narrow panel
        <Box p="3" className={styles.dropdownHeader}>
          <Select.Root value={selectedPluginId} onValueChange={(value) => onTabChange?.(value)}>
            <Select.Trigger className={styles.dropdownTrigger}>
              {activeView && <activeView.tabLabelComponent artifacts={artifacts} shouldShowIcon={true} />}
            </Select.Trigger>
            <Select.Content>
              {enabledArtifactViews.map((artifactView) => (
                <Select.Item key={artifactView.id} value={artifactView.id}>
                  <artifactView.tabLabelComponent artifacts={artifacts} shouldShowIcon={true} />
                </Select.Item>
              ))}
            </Select.Content>
          </Select.Root>
        </Box>
      ) : (
        // Tabs header for wide panel
        <Tabs.Root
          value={selectedPluginId}
          onValueChange={(value) => onTabChange?.(value)}
          className={styles.tabsHeader}
        >
          <Tabs.List color="gold" className={styles.tabsList}>
            {enabledArtifactViews.map((artifactView) => (
              <Tabs.Trigger
                key={artifactView.id}
                value={artifactView.id}
                data-testid={`ARTIFACT_${artifactView.id.toUpperCase()}_TAB`}
                className={flashingTabId === artifactView.id ? styles.flashTab : undefined}
              >
                <artifactView.tabLabelComponent artifacts={artifacts} shouldShowIcon={!shouldHideIcons} />
              </Tabs.Trigger>
            ))}
          </Tabs.List>
        </Tabs.Root>
      )}

      <Box className={styles.contentContainer} p="4" flexGrow="1">
        {enabledArtifactViews.map((artifactView) => (
          <Box
            key={artifactView.id}
            className={styles.contentPanel}
            style={{ display: artifactView.id === selectedPluginId ? "flex" : "none" }}
            data-testid={`ARTIFACT_${artifactView.id.toUpperCase()}_SECTION`}
          >
            <ScrollArea className={styles.scrollArea} scrollbars="vertical">
              <Box p="4" maxWidth="100%" minWidth="0" height="100%">
                {/*FIXME: we shouldn't need to pass the task in here. All the data should be through the artifact.*/}
                {/* This is here bc of the terminal artifact, to get the ttyd url. We should just make an artifact for that instead */}
                <artifactView.contentComponent
                  artifacts={artifacts}
                  checksData={checksData}
                  task={task}
                  userMessageIds={userMessageIds}
                  appendTextRef={appendTextRef}
                  checksDefinedForMessage={checksDefinedForMessage}
                />
              </Box>
            </ScrollArea>
          </Box>
        ))}
      </Box>
    </Flex>
  );
};
