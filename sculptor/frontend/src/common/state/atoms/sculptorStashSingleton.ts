import { atom } from "jotai";

import type { SculptorStashSingleton } from "../../../api";

export type SculptorStashSingletonState = {
  isOtherProjectStashed: boolean;
  stashSingleton: SculptorStashSingleton;
};

export const sculptorStashSingletonStateAtom = atom<SculptorStashSingletonState | null>(null);
