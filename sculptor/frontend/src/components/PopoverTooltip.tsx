import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { Box, Theme } from "@radix-ui/themes";
import { cloneElement, forwardRef, type ReactElement, type ReactNode } from "react";

import styles from "./PopoverTooltip.module.scss";

type PopoverTooltipProps = {
  content: ReactNode;
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  align?: "start" | "center" | "end";
  children: ReactElement;
};

export const PopoverTooltip = forwardRef<HTMLElement, PopoverTooltipProps>(
  ({ children, content, open, defaultOpen, onOpenChange, align = "center" }, ref): ReactElement => {
    return (
      <TooltipPrimitive.Provider delayDuration={1000} skipDelayDuration={500}>
        <TooltipPrimitive.Root
          open={open || (content ? undefined : false)}
          defaultOpen={defaultOpen}
          onOpenChange={onOpenChange}
        >
          <TooltipPrimitive.Trigger asChild>{cloneElement(children, { ref })}</TooltipPrimitive.Trigger>

          <TooltipPrimitive.Portal>
            <Theme asChild>
              <TooltipPrimitive.Content side="top" align={align} sideOffset={6} collisionPadding={8}>
                <Box className={styles.container}>{content}</Box>
              </TooltipPrimitive.Content>
            </Theme>
          </TooltipPrimitive.Portal>
        </TooltipPrimitive.Root>
      </TooltipPrimitive.Provider>
    );
  },
);
