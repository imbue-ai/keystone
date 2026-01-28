import type { CodingAgentTaskView, Project } from "../../api";

// TODO: remove pointless getter?
export const getBranchName = (task: CodingAgentTaskView): string | null => {
  return task.branchName;
};

export const getHumanDuration = (syncStartedAt: string | null): string => {
  if (!syncStartedAt) return "";

  const startTime = new Date(syncStartedAt).getTime();
  const now = Date.now();
  const diffInSeconds = Math.floor((now - startTime) / 1000);

  if (diffInSeconds < 60) {
    return "";
  }

  const diffInMinutes = Math.floor(diffInSeconds / 60);
  if (diffInMinutes < 360) {
    return `${diffInMinutes} min`;
  }

  const diffInHours = Math.floor(diffInMinutes / 60);
  if (diffInHours < 24) {
    return `${diffInHours} hours`;
  }

  const diffInDays = Math.floor(diffInHours / 24);
  return `${diffInDays}d`;
};

export const getSyncingDuration = (syncStartedAt: string | null): string => {
  const durationInUnits = getHumanDuration(syncStartedAt);
  if (durationInUnits === "") {
    return "";
  }
  return `on for ${durationInUnits}`;
};

export type DiffStats = {
  additions: number;
  deletions: number;
};

export const calculateDiffStats = (diff: string): DiffStats => {
  const lines = diff.split("\n");
  let additions = 0;
  let deletions = 0;

  lines.forEach((line) => {
    if (line.startsWith("+") && !line.startsWith("+++")) {
      additions++;
    } else if (line.startsWith("-") && !line.startsWith("---")) {
      deletions++;
    }
  });

  return { additions, deletions };
};

export const getProjectPath = (project: Project): string => {
  const fullPath = project?.userGitRepoUrl || "";
  // Remove "file://" prefix if it exists
  const parts = fullPath.split("file://");
  return parts.length > 1 ? parts[1] : fullPath;
};
