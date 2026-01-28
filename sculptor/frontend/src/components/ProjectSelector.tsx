import { Flex, Select, Text } from "@radix-ui/themes";
import { PlusIcon } from "lucide-react";
import type { ReactElement } from "react";
import { useState } from "react";

import { ElementIds } from "../api";
import { useImbueNavigate, useProjectPageParams } from "../common/NavigateUtils.ts";
import { useProjects } from "../common/state/hooks/useProjects.ts";
import { AddProjectDialog } from "./AddProjectDialog.tsx";
import styles from "./ProjectSelector.module.scss";
import type { ToastContent } from "./Toast.tsx";
import { Toast, ToastType } from "./Toast.tsx";

const truncatePath = (path: string, maxLength: number = 50): string => {
  if (path.length <= maxLength) {
    return path;
  }
  const truncated = path.slice(-(maxLength - 4));
  const firstSlash = truncated.indexOf("/");
  if (firstSlash !== -1) {
    return ".../" + truncated.slice(firstSlash + 1);
  }
  return ".../" + truncated;
};

const _NEW_PROJECT_SELECT_VALUE = "_NEW_PROJECT_SELECT_VALUE";

export const ProjectSelector = (): ReactElement => {
  const { projectID } = useProjectPageParams();
  const { navigateToHome } = useImbueNavigate();
  const allProjects = useProjects();
  const [toast, setToast] = useState<ToastContent | null>(null);
  const [shouldShowAddProjectDialog, setShouldShowAddProjectDialog] = useState(false);

  const handleProjectChange = async (value: string): Promise<void> => {
    if (value === _NEW_PROJECT_SELECT_VALUE) {
      setShouldShowAddProjectDialog(true);
      return;
    }

    const selectedProject = allProjects.find((p) => p.objectId === value);
    if (!selectedProject) {
      console.error("Project not found:", value);
      return;
    }

    try {
      if (!selectedProject.userGitRepoUrl) {
        return;
      }
      navigateToHome(selectedProject.objectId);
    } catch (error) {
      console.error("Failed to set current project:", error);
      setToast({ title: "Failed to switch project", type: ToastType.ERROR });
    }
  };

  const currentProject = allProjects.find((project) => project.objectId === projectID);
  return (
    <>
      <Select.Root value={currentProject?.objectId} onValueChange={handleProjectChange} disabled={!allProjects.length}>
        <Select.Trigger variant="surface" className={styles.projectSelector} data-testid={ElementIds.PROJECT_SELECTOR}>
          {currentProject?.name}
        </Select.Trigger>
        <Select.Content position="popper" className={styles.selectContent}>
          {allProjects.map((project) => {
            const fullPath = project.userGitRepoUrl?.replace(/^file:\/\//, "") ?? "";
            const displayPath = truncatePath(fullPath);

            return (
              <Select.Item
                key={project.objectId}
                value={project.objectId}
                data-testid={ElementIds.PROJECT_SELECT_ITEM}
                className={styles.projectItem}
              >
                <Flex direction="column" gap="0">
                  <Text weight="medium" className={styles.projectName}>
                    {project.name}
                  </Text>
                  {displayPath && (
                    <Text size="1" className={styles.projectPath}>
                      {displayPath}
                    </Text>
                  )}
                </Flex>
              </Select.Item>
            );
          })}
          <Select.Separator className={styles.newRepoSeparator} />
          <Select.Item value={_NEW_PROJECT_SELECT_VALUE} data-testid={ElementIds.OPEN_NEW_REPO_BUTTON}>
            <Flex direction="row" align="center" gapX="2">
              <PlusIcon />
              <Text>Open New Repo</Text>
            </Flex>
          </Select.Item>
        </Select.Content>
      </Select.Root>

      {shouldShowAddProjectDialog && (
        <AddProjectDialog setToast={setToast} setShouldShowAddProjectDialog={setShouldShowAddProjectDialog} />
      )}

      <Toast open={!!toast} onOpenChange={(open) => !open && setToast(null)} title={toast?.title} type={toast?.type} />
    </>
  );
};
