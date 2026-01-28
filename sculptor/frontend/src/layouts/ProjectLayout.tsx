import { Flex } from "@radix-ui/themes";
import { useAtomValue, useSetAtom } from "jotai";
import type { ReactElement } from "react";
import { useEffect, useRef, useState } from "react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { Outlet } from "react-router-dom";

import { InstructionalModal } from "~/pages/chat/components/InstructionalModal.tsx";

import { NARROW_PROJECT_LAYOUT_BREAKPOINT, PROJECT_LAYOUT_ID } from "../common/Constants.ts";
import { isDownStatus } from "../common/Guards.ts";
import { useComponentWidth } from "../common/Hooks.ts";
import { useProjectPageParams } from "../common/NavigateUtils.ts";
import { backendStatusAtom } from "../common/state/atoms/backend.ts";
import { isSidebarOpenAtom } from "../common/state/atoms/sidebar.ts";
import { useLocalSyncTaskStatePolling } from "../common/state/hooks/useLocalSyncTaskStatePolling.ts";
import { useProject } from "../common/state/hooks/useProjects.ts";
import { useDockerStatus, useProviderStatusesPoll } from "../common/state/hooks/useProviderStatusesPoll.ts";
import { useUnifiedStream } from "../common/state/hooks/useUnifiedStream";
import { NotificationToasts } from "../components/NotificationToasts.tsx";
import { ProjectPathDialog } from "../components/ProjectPathDialog.tsx";
import { SearchModal } from "../components/SearchModal.tsx";
import { Sidebar } from "../components/Sidebar.tsx";
import { SyncedTaskFooter } from "../components/SyncedTaskFooter.tsx";
import { TaskModal } from "../components/TaskModal.tsx";
import { WarningStatusBanner } from "../components/WarningStatusBanner.tsx";
import { useProjectLayoutKeyboardShortcuts } from "./hooks/useProjectLayoutKeyboardShortcuts.ts";
import styles from "./ProjectLayout.module.scss";

export const ProjectLayout = (): ReactElement => {
  const isSidebarOpen = useAtomValue(isSidebarOpenAtom);
  const setIsSidebarOpen = useSetAtom(isSidebarOpenAtom);
  const backendStatus = useAtomValue(backendStatusAtom);
  const { projectID, taskID } = useProjectPageParams();
  const dockerStatus = useDockerStatus();
  const { ref: containerRef, width: containerWidth } = useComponentWidth(PROJECT_LAYOUT_ID);
  const currentProject = useProject(projectID);
  const [isProjectPathDialogOpen, setIsProjectPathDialogOpen] = useState(false);
  const sidebarRef = useRef<HTMLDivElement>(null);

  useUnifiedStream();
  useProviderStatusesPoll();
  useProjectLayoutKeyboardShortcuts();
  useLocalSyncTaskStatePolling({ currentProjectID: projectID });

  const isNarrowLayout = containerWidth < NARROW_PROJECT_LAYOUT_BREAKPOINT;
  const prevIsNarrowLayoutRef = useRef<boolean>(isNarrowLayout);

  // Auto-collapse sidebar when transitioning from wide to narrow layout (during resize)
  useEffect(() => {
    // eslint-disable-next-line @typescript-eslint/naming-convention
    const wasWideLayout = !prevIsNarrowLayoutRef.current;
    // Only auto-collapse when transitioning from wide to narrow, not when already narrow

    if (wasWideLayout && isNarrowLayout && isSidebarOpen) {
      setIsSidebarOpen(false);
    }

    prevIsNarrowLayoutRef.current = isNarrowLayout;
  }, [isNarrowLayout, isSidebarOpen, setIsSidebarOpen]);

  const hasBackendStopped = backendStatus.status === "unresponsive";
  const hasHealthWarningOnBackend = backendStatus.status === "warning";

  const isProjectPathInaccessible = currentProject && currentProject.isPathAccessible === false;

  return (
    <>
      <Flex direction="column" height="100vh" width="100vw" ref={containerRef} position="relative">
        <PanelGroup direction="horizontal" className={styles.panelGroup}>
          {!isNarrowLayout && isSidebarOpen && (
            <>
              <Panel id="sidebar" defaultSize={22} maxSize={50} order={1} style={{ minWidth: "320px" }}>
                <Sidebar />
              </Panel>
              <PanelResizeHandle className={styles.resizeHandle} />
            </>
          )}
          <Panel id="main" order={2}>
            <Outlet />
          </Panel>
        </PanelGroup>
        {/* NOTE (IMPORTANT): The narrow-layout-sidebar MUST come after the PanelGroup in the HTML code, otherwise
        The drag events side of the PanelGroup will eat some mouse events in the Sidebar. Very esoteric.
        See here for more info: https://github.com/electron/electron/issues/1354
        */}
        {isNarrowLayout && isSidebarOpen && (
          <>
            {/* Overlay to handle clicks outside the sidebar */}
            <div className={styles.sidebarOverlay} onClick={() => setIsSidebarOpen(false)} />
            <div ref={sidebarRef} className={styles.narrowSidebar} onClick={(e) => e.stopPropagation()}>
              <Sidebar />
            </div>
          </>
        )}
        <SyncedTaskFooter projectID={projectID} currentTaskID={taskID} />
        {isProjectPathInaccessible && (
          <WarningStatusBanner
            message={`Project folder not found: ${currentProject.name}.`}
            linkText="Learn more"
            onLinkClick={() => setIsProjectPathDialogOpen(true)}
          />
        )}
        {dockerStatus && isDownStatus(dockerStatus.status) && (
          <WarningStatusBanner message="Docker not detected, ensure that Docker is installed and running." />
        )}
        {(hasBackendStopped || hasHealthWarningOnBackend) && (
          <WarningStatusBanner message={backendStatus.payload.message} />
        )}
      </Flex>
      <TaskModal />
      <SearchModal />
      <InstructionalModal />
      <ProjectPathDialog
        isOpen={isProjectPathDialogOpen}
        project={currentProject}
        onClose={() => setIsProjectPathDialogOpen(false)}
      />
      {/* Notification display depends on current project, that's why it's here */}
      <NotificationToasts />
    </>
  );
};
