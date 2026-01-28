import { atom } from "jotai";

import { type SyncedTaskView } from "~/api";

export type LocalSyncState = {
  syncedTask: SyncedTaskView;
  isOtherProjectSynced: boolean;
};

// TODO(mjr): replace callsites with a new useLocalSyncState to match convention
export const localSyncStateAtom = atom<LocalSyncState | null>(null);
