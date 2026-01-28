import { useAtomValue } from "jotai";

import {
  CHAT_PAGE_ID,
  NARROW_CHAT_PAGE_BREAKPOINT,
  NARROW_PROJECT_LAYOUT_BREAKPOINT,
  PROJECT_LAYOUT_ID,
} from "../../Constants.ts";
import { componentWidthAtomFamily } from "../atoms/responsiveLayout.ts";

export const useComponentWidthById = (componentID: string | undefined): number | null => {
  return useAtomValue(componentWidthAtomFamily(componentID));
};

export const useIsNarrowLayout = (): boolean => {
  const chatPageWidth = useAtomValue(componentWidthAtomFamily(CHAT_PAGE_ID));
  const projectLayoutWidth = useAtomValue(componentWidthAtomFamily(PROJECT_LAYOUT_ID));

  return !!(
    chatPageWidth &&
    projectLayoutWidth &&
    (chatPageWidth < NARROW_CHAT_PAGE_BREAKPOINT || projectLayoutWidth < NARROW_PROJECT_LAYOUT_BREAKPOINT)
  );
};
