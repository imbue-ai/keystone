import { type ButtonProps, IconButton, Tooltip, type TooltipProps } from "@radix-ui/themes";
import type { PropsWithChildren, ReactElement } from "react";
import { forwardRef } from "react";

import type { PropsWithClassName } from "../common/Types.ts";
import { neutral } from "../common/Utils.ts";

type TooltipIconProps = {
  icon?: ReactElement;
  tooltipText: string;
  // defined in PopperContentProps
  side?: TooltipProps["side"];
  align?: TooltipProps["align"];
} & PropsWithChildren;

export const TooltipIcon = (props: TooltipIconProps): ReactElement => {
  const { tooltipText, icon, children, ...tooltipProps } = props;
  return (
    <Tooltip content={tooltipText} {...tooltipProps}>
      {icon ?? children}
    </Tooltip>
  );
};

type TooltipIconButtonProps = TooltipIconProps &
  PropsWithClassName & {
    onClick?: (e: React.MouseEvent<HTMLButtonElement>) => void;
    color?: ButtonProps["color"];
    variant?: ButtonProps["variant"];
    disabled?: ButtonProps["disabled"];
    loading?: ButtonProps["loading"];
    size?: ButtonProps["size"];
    style?: React.CSSProperties;
  };

export const TooltipIconButton = forwardRef<HTMLButtonElement, TooltipIconButtonProps>((props, ref): ReactElement => {
  const {
    className,
    tooltipText,
    icon,
    onClick,
    children,
    color,
    variant,
    // eslint-disable-next-line @typescript-eslint/naming-convention
    disabled,
    // eslint-disable-next-line @typescript-eslint/naming-convention
    loading,
    size,
    side,
    align,
    style,
    ...rest
  } = props;

  // TODO: there's a weird bug when using the tooltip in a dropdown menu where it stays visible longer than it should
  return (
    <Tooltip content={tooltipText} side={side} align={align}>
      <IconButton
        ref={ref}
        variant={variant ?? "ghost"}
        disabled={disabled ?? false}
        loading={loading ?? false}
        size={size ?? "1"}
        onClick={onClick}
        color={color ?? neutral}
        className={className}
        style={style}
        {...rest}
      >
        {icon ?? children}
      </IconButton>
    </Tooltip>
  );
});
