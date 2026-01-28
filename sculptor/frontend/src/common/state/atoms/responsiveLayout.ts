import type { PrimitiveAtom } from "jotai";
import { atom } from "jotai";
import { atomFamily } from "jotai/utils";

export const componentWidthAtomFamily = atomFamily<string | undefined, PrimitiveAtom<number | null>>(() =>
  atom<number | null>(null),
);
