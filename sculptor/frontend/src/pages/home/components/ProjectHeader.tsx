import { Heading } from "@radix-ui/themes";
import type { ReactElement } from "react";

import { ElementIds } from "../../../api";
import { useProjectPageParams } from "../../../common/NavigateUtils";
import { useProject } from "../../../common/state/hooks/useProjects";
import { getProjectPath } from "../Utils";
import styles from "./ProjectHeader.module.scss";

export const ProjectHeader = (): ReactElement => {
  const { projectID } = useProjectPageParams();
  const project = useProject(projectID);
  const projectPath = project ? getProjectPath(project) : "";

  return (
    <>
      <Heading className={styles.titleText}>{project?.name}</Heading>
      <Heading className={styles.subtitleText} mb="1" data-testid={ElementIds.REPO_INDICATOR}>
        {projectPath}
      </Heading>
    </>
  );
};
