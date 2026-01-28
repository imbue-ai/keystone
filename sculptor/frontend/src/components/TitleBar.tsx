import { Flex } from "@radix-ui/themes";
import { useAtom } from "jotai";
import { PanelLeftIcon } from "lucide-react";
import type { ReactElement } from "react";

import { ElementIds } from "../api";
import { isSidebarOpenAtom } from "../common/state/atoms/sidebar.ts";
import { getMetaKey, getTitleBarLeftPadding, TITLEBAR_HEIGHT } from "../electron/utils.ts";
import styles from "./TitleBar.module.scss";
import { TooltipIconButton } from "./TooltipIconButton.tsx";

type TitleBarProps = {
  leftPadding?: string;
  shouldShowToggleSidebarButton?: boolean;
};

/**
 * Reusable titlebar component that provides a draggable region
 * for custom window controls on macOS
 */
export const TitleBar = ({ leftPadding, shouldShowToggleSidebarButton = false }: TitleBarProps): ReactElement => {
  const [isSidebarOpen, setIsSidebarOpen] = useAtom(isSidebarOpenAtom);
  const padding = leftPadding ?? getTitleBarLeftPadding(isSidebarOpen);
  const metaKey = getMetaKey();

  return (
    <Flex
      position="absolute"
      top="0"
      left="0"
      width="100%"
      pl={padding}
      height={`${TITLEBAR_HEIGHT}px`}
      align="center"
      className={styles.draggable}
    >
      {shouldShowToggleSidebarButton && (
        <TooltipIconButton
          tooltipText={`Toggle sidebar (${metaKey}B)`}
          variant="ghost"
          onClick={() => setIsSidebarOpen(!isSidebarOpen)}
          aria-label="Toggle sidebar"
          className={styles.nonDraggable}
          data-state={isSidebarOpen ? "open" : "closed"}
          data-testid={ElementIds.TOGGLE_SIDEBAR_BUTTON}
        >
          <PanelLeftIcon />
        </TooltipIconButton>
      )}
    </Flex>
  );
};
