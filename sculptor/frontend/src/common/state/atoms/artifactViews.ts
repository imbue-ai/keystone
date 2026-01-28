import { atom } from "jotai";

import {
  defaultEnabledArtifactViewIds,
  registeredArtifactViews,
} from "../../../pages/chat/components/artifacts/Registry";
import type { ArtifactView } from "../../../pages/chat/Types.ts";

// Store enabled artifact views (initialized with defaults)
export const enabledArtifactViewsAtom = atom<ReadonlyArray<ArtifactView>>(
  registeredArtifactViews.filter((v) => defaultEnabledArtifactViewIds.includes(v.id)),
);
