import type { PrimitiveAtom } from "jotai";
import { atom } from "jotai";
import { atomFamily } from "jotai/utils";

import type { LocalRepoInfo } from "../../../api";
import type { ProjectID } from "../../Types.ts";

export const localRepoInfoAtomFamily = atomFamily<ProjectID, PrimitiveAtom<LocalRepoInfo | null>>(() =>
  atom<LocalRepoInfo | null>(null),
);

export const updateLocalRepoInfoAtom = atom(
  null,
  (getAtom, setAtom, update: { projectId: ProjectID; repoInfo: LocalRepoInfo | null }) => {
    const atomForProject = localRepoInfoAtomFamily(update.projectId);
    setAtom(atomForProject, update.repoInfo);
  },
);
