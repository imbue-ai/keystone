import { DropdownMenu } from "@radix-ui/themes";
import { CircleHelpIcon } from "lucide-react";
import type { ReactElement } from "react";

import { useInstructionalModal } from "~/common/state/hooks/useInstructionalModal";

import { TooltipIconButton } from "./TooltipIconButton";

export const HelpMenu = (): ReactElement => {
  const { showInstructionalModal } = useInstructionalModal();
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger>
        <TooltipIconButton tooltipText="Help" variant="ghost">
          <CircleHelpIcon />
        </TooltipIconButton>
      </DropdownMenu.Trigger>
      <DropdownMenu.Content>
        <DropdownMenu.Item
          onSelect={() => {
            return window.open("https://discord.gg/sBAVvHPUTE", "_blank");
          }}
        >
          Discord Community
        </DropdownMenu.Item>
        <DropdownMenu.Item
          onSelect={() => {
            return window.open("https://github.com/imbue-ai/sculptor", "_blank");
          }}
        >
          Support Docs
        </DropdownMenu.Item>
        <DropdownMenu.Item
          onSelect={() => {
            return window.open("https://imbue-1.gitbook.io/imbue-docs/changelog", "_blank");
          }}
        >
          Changelog
        </DropdownMenu.Item>
        <DropdownMenu.Item
          onSelect={() => {
            showInstructionalModal();
          }}
        >
          Tutorial Video
        </DropdownMenu.Item>
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  );
};
