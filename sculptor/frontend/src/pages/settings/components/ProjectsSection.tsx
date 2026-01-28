import { Box, Button, Flex, Separator, Spinner, Text } from "@radix-ui/themes";
import { useSetAtom } from "jotai";
import { Plus } from "lucide-react";
import type { ReactElement } from "react";
import { useCallback, useEffect, useState } from "react";

import { HTTPException } from "~/common/Errors.ts";
import { useImbueNavigate, useProjectPageParams } from "~/common/NavigateUtils.ts";
import { removeProjectAtom } from "~/common/state/atoms/projects.ts";
import { AddProjectDialog } from "~/components/AddProjectDialog.tsx";

import { type CodingAgentTaskView, deleteProject, type Project } from "../../../api";
import { useProjects } from "../../../common/state/hooks/useProjects.ts";
import type { ToastContent } from "../../../components/Toast.tsx";
import { ToastType } from "../../../components/Toast.tsx";
import { ProjectRow } from "./ProjectRow.tsx";
import styles from "./ProjectsSection.module.scss";
import { RemoveProjectDialog } from "./RemoveProjectDialog.tsx";

type ProjectAgentCounts = {
  active: number;
  archived: number;
};

type RemoveDialogState = {
  isOpen: boolean;
  projectId: string | null;
  projectName: string | null;
  activeAgentCount: number;
  archivedAgentCount: number;
  isDeleting: boolean;
};

export const ProjectsSection = ({ setToast }: { setToast: (toast: ToastContent | null) => void }): ReactElement => {
  const projects = useProjects();
  const { projectID: currentProjectId } = useProjectPageParams();
  const [agentCounts, setAgentCounts] = useState<Record<string, ProjectAgentCounts>>({});
  const [isLoadingCounts, setIsLoadingCounts] = useState(true);
  const { navigateToHome } = useImbueNavigate();
  const removeProjectFromState = useSetAtom(removeProjectAtom);
  const [removeDialogState, setRemoveDialogState] = useState<RemoveDialogState>({
    isOpen: false,
    projectId: null,
    projectName: null,
    activeAgentCount: 0,
    archivedAgentCount: 0,
    isDeleting: false,
  });
  const [shouldShowAddProjectDialog, setShouldShowAddProjectDialog] = useState(false);

  // Fetch agent counts for all projects
  useEffect(() => {
    const fetchAgentCounts = async (): Promise<void> => {
      setIsLoadingCounts(true);
      const counts: Record<string, ProjectAgentCounts> = {};

      for (const project of projects) {
        try {
          const response = await fetch(`/api/v1/projects/${project.objectId}/tasks`);
          if (response.ok) {
            const tasks = (await response.json()) as Array<CodingAgentTaskView>;
            counts[project.objectId] = {
              active: tasks.filter((task) => !task.isArchived && !task.isDeleted).length,
              archived: tasks.filter((task) => task.isArchived && !task.isDeleted).length,
            };
          } else {
            counts[project.objectId] = { active: 0, archived: 0 };
          }
        } catch (error) {
          console.error(`Failed to fetch tasks for project ${project.objectId}:`, error);
          counts[project.objectId] = { active: 0, archived: 0 };
        }
      }

      setAgentCounts(counts);
      setIsLoadingCounts(false);
    };

    if (projects.length > 0) {
      fetchAgentCounts();
    } else {
      setIsLoadingCounts(false);
    }
  }, [projects]);

  const handleRemoveClick = useCallback(
    (project: Project) => {
      const counts = agentCounts[project.objectId] || { active: 0, archived: 0 };
      setRemoveDialogState({
        isOpen: true,
        projectId: project.objectId,
        projectName: project.name,
        activeAgentCount: counts.active,
        archivedAgentCount: counts.archived,
        isDeleting: false,
      });
    },
    [agentCounts],
  );

  const handleRemoveConfirm = useCallback(async () => {
    const projectIdToDelete = removeDialogState.projectId;
    if (!projectIdToDelete || removeDialogState.isDeleting) return;

    // Set deleting state
    setRemoveDialogState((prev) => ({ ...prev, isDeleting: true }));

    try {
      // Call the delete endpoint
      await deleteProject({
        path: { project_id: projectIdToDelete },
      });
      removeProjectFromState(projectIdToDelete);

      // Close the dialog
      setRemoveDialogState({
        isOpen: false,
        projectId: null,
        projectName: null,
        activeAgentCount: 0,
        archivedAgentCount: 0,
        isDeleting: false,
      });

      // Show success message
      setToast({
        type: ToastType.SUCCESS,
        title: "Repository removed successfully",
      });

      // If we deleted the current project, redirect to another project or the project selection page
      if (projectIdToDelete === currentProjectId) {
        const remainingProjects = projects.filter((p) => p.objectId !== projectIdToDelete);
        if (remainingProjects.length > 0) {
          // Navigate to the first remaining project
          navigateToHome(remainingProjects[0].objectId);
        } else {
          // No projects left, navigate to project selection page
          window.location.href = "/#/projects";
        }
      }
    } catch (error) {
      let errorMessage = "Failed to remove repository";
      if (error instanceof HTTPException) {
        errorMessage = error.detail;
      } else if (error instanceof Error) {
        errorMessage = error.message;
      }
      setToast({
        type: ToastType.ERROR,
        title: errorMessage,
      });
      // Reset deleting state on error
      setRemoveDialogState((prev) => ({ ...prev, isDeleting: false }));
    }
  }, [
    removeDialogState.projectId,
    removeDialogState.isDeleting,
    currentProjectId,
    projects,
    navigateToHome,
    setToast,
    removeProjectFromState,
  ]);

  const handleRemoveCancel = useCallback(() => {
    if (removeDialogState.isDeleting) return; // Prevent closing while deleting
    setRemoveDialogState({
      isOpen: false,
      projectId: null,
      projectName: null,
      activeAgentCount: 0,
      archivedAgentCount: 0,
      isDeleting: false,
    });
  }, [removeDialogState.isDeleting]);

  if (isLoadingCounts) {
    return (
      <Flex direction="column" className={styles.projectsSection} px="7">
        <Box className={styles.loadingState}>
          <Spinner size="3" />
        </Box>
      </Flex>
    );
  }

  return (
    <>
      <Flex direction="column" className={styles.projectsSection} px="7">
        {projects.length === 0 ? (
          <Box className={styles.emptyState}>
            <Text size="2">No repositories found. Open a repository to get started.</Text>
          </Box>
        ) : (
          <Box className={styles.projectsList}>
            {projects.map((project) => {
              const counts = agentCounts[project.objectId] || { active: 0, archived: 0 };
              const projectPath = project.userGitRepoUrl?.replace("file://", "") || "";
              return (
                <ProjectRow
                  key={project.objectId}
                  projectName={project.name}
                  projectPath={projectPath}
                  activeAgentCount={counts.active}
                  archivedAgentCount={counts.archived}
                  isPathAccessible={project.isPathAccessible ?? false}
                  onRemove={() => handleRemoveClick(project)}
                />
              );
            })}
          </Box>
        )}
        {projects.length > 0 && <Separator size="4" className={styles.separator} />}
        <Box>
          <Button variant="soft" onClick={() => setShouldShowAddProjectDialog(true)} className={styles.openNewButton}>
            <Plus size={16} />
            Open new repo
          </Button>
          {shouldShowAddProjectDialog && (
            <AddProjectDialog setToast={setToast} setShouldShowAddProjectDialog={setShouldShowAddProjectDialog} />
          )}
        </Box>
      </Flex>

      <RemoveProjectDialog
        isOpen={removeDialogState.isOpen}
        onClose={handleRemoveCancel}
        onConfirm={handleRemoveConfirm}
        projectName={removeDialogState.projectName || ""}
        activeAgentCount={removeDialogState.activeAgentCount}
        archivedAgentCount={removeDialogState.archivedAgentCount}
        isDeleting={removeDialogState.isDeleting}
      />
    </>
  );
};
