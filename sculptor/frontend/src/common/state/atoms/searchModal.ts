import { atom } from "jotai";
import { atomWithStorage } from "jotai/utils";

export const searchModalOpenAtom = atom(false);

export const searchModalContentsAtom = atomWithStorage<string>("sculptor-search-modal-contents", "");
