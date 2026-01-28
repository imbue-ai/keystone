import { atom } from "jotai";

/**
 * Atom tracking whether smooth streaming should be enabled for the active message.
 * Set to `true` when the message's tail is in-view so we drip text, or `false` when
 * the message is off-screen so we render snapshots immediately.
 */
export const isSmoothStreamingEnabledAtom = atom<boolean>(true);
