import type { ButtonProps } from "@radix-ui/themes";
import { DropdownMenu, Flex, IconButton } from "@radix-ui/themes";
import { snakeCase } from "lodash";
import { ChevronDown } from "lucide-react";
import type { PropsWithChildren, ReactElement } from "react";
import type React from "react";

import { useHover } from "../common/Hooks.ts";
import type { PropsWithClassName } from "../common/Types.ts";
import { getDataAttributesFromProps, mergeClasses, neutral, optional } from "../common/Utils.ts";
import styles from "./ComboButton.module.scss";
import { PendingButton } from "./PendingButton.tsx";

export type ComboButtonOption = {
  label: string | ReactElement;
  disabled?: boolean;
  onClick?: () => void | Promise<void>;
};

type ComboButtonProps = {
  options?: ReadonlyArray<ComboButtonOption>;
  size?: ButtonProps["size"];
  variant?: ButtonProps["variant"];
  color?: ButtonProps["color"];
  disabled?: ButtonProps["disabled"];
  loading?: ButtonProps["loading"];
  onClick?: () => void | Promise<void>;
} & PropsWithClassName &
  PropsWithChildren;

export const ComboButton = (props: ComboButtonProps): React.ReactElement => {
  const { children, className, options, ...other } = props;
  const { dataAttributes, rest: buttonProps } = getDataAttributesFromProps<ComboButtonProps>(other);

  const { isHovered, hoverProps } = useHover();
  const isRenderingOptions = !!options && options.length > 0;
  const isDisabled = !!buttonProps.disabled || !!buttonProps.loading;

  // FIXME: the box shadow itself is not perfectly smooth / looks great because of the difference between spread and offsets (incredibly cringe I know)
  return (
    <Flex>
      <PendingButton
        className={mergeClasses(
          className,
          optional(isRenderingOptions, styles.comboButton),
          optional(isHovered, styles.buttonHovered),
        )}
        {...dataAttributes}
        {...buttonProps}
        {...hoverProps}
        disabled={isDisabled}
      >
        {children}
      </PendingButton>
      {isRenderingOptions && (
        <DropdownMenu.Root>
          <DropdownMenu.Trigger>
            <IconButton
              {...buttonProps}
              loading={false}
              disabled={isDisabled}
              data-disabled={isDisabled ? "true" : undefined}
              className={mergeClasses(className, styles.comboOptionsButton, optional(isHovered, styles.buttonHovered))}
            >
              <ChevronDown />
            </IconButton>
          </DropdownMenu.Trigger>
          <DropdownMenu.Content size="1" color={neutral}>
            {options.map((option: ComboButtonOption, index: number) => (
              <DropdownMenu.Item
                key={typeof option.label === "string" ? snakeCase(option.label) : index}
                disabled={option.disabled}
                onClick={option.disabled ? undefined : option.onClick}
                className={mergeClasses(optional(!!option.disabled, styles.dropdownDisabled))}
              >
                {option.label}
              </DropdownMenu.Item>
            ))}
          </DropdownMenu.Content>
        </DropdownMenu.Root>
      )}
    </Flex>
  );
};
