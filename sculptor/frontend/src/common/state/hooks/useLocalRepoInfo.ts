import { useAtomValue } from "jotai";
import { useMemo } from "react";

import type { LocalRepoInfo } from "../../../api";
import type { ProjectID } from "../../Types";
import { localRepoInfoAtomFamily } from "../atoms/localRepoInfo.ts";

export const useLocalRepoInfo = (projectId: ProjectID): LocalRepoInfo | null => {
  const atom = useMemo(() => localRepoInfoAtomFamily(projectId), [projectId]);
  return useAtomValue(atom);
};
