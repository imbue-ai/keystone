import { atom } from "jotai";

import { sculptorSettingsAtom } from "./sculptorSettings.ts";

export const globalDevModeAtom = atom((get) => {
  const sculptorSettings = get(sculptorSettingsAtom);
  return sculptorSettings?.DEV_MODE;
});
