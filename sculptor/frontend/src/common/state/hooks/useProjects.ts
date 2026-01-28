import { useAtomValue } from "jotai";

import type { ProjectID } from "~/common/Types";

import type { Project } from "../../../api";
import { projectAtomFamily, projectsArrayAtom } from "../atoms/projects";

export const useProject = (projectId: string): Project | null => {
  return useAtomValue(projectAtomFamily(projectId));
};

export const useProjects = (): ReadonlyArray<Project> => {
  return useAtomValue(projectsArrayAtom);
};

export const useProjectPath = (projectID: ProjectID): string => {
  const project = useProject(projectID);
  if (!project) {
    return "";
  }
  // TODO: why is project path nullable ever?
  return project?.userGitRepoUrl?.replace(/^file:\/\//, "") ?? "";
};
