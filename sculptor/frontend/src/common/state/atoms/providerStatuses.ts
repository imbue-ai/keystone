import { atom } from "jotai";

import type { ProviderStatusInfo } from "../../../api";

export const providerStatusesAtom = atom<Array<ProviderStatusInfo> | null>(null);
