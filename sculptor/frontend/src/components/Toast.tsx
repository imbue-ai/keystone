import * as ToastPrimitive from "@radix-ui/react-toast";
import { Flex } from "@radix-ui/themes";
import { X } from "lucide-react";
import type React from "react";
import type { PropsWithChildren, ReactNode } from "react";

import { ElementIds } from "../api";
import { mergeClasses } from "../common/Utils.ts";
import styles from "./Toast.module.scss";

export const ToastType = {
  DEFAULT: "DEFAULT",
  SUCCESS: "SUCCESS",
  ERROR: "ERROR",
  WARNING: "WARNING",
} as const;

export type ToastType = (typeof ToastType)[keyof typeof ToastType];

export type ToastContent = {
  title: string;
  description?: ReactNode;
  type?: ToastType;
};

export type ToastProps = {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  duration?: number;
  type?: ToastType;
  title?: string;
  description?: ReactNode;
  action?: {
    label: string;
    handleClick: () => void;
  };
} & PropsWithChildren;

export const Toast = ({
  children,
  open,
  onOpenChange,
  duration = 3000,
  type = ToastType.DEFAULT,
  title,
  description,
  action,
}: ToastProps): React.ReactElement => {
  return (
    <ToastPrimitive.Root
      className={mergeClasses(styles.root, styles[type])}
      open={open}
      onOpenChange={onOpenChange}
      duration={duration}
      onClick={(e) => e.stopPropagation()}
      data-testid={ElementIds.TOAST}
    >
      <Flex className={styles.content} align="center" gap="2" justify="start">
        {title && <ToastPrimitive.Title className={styles.title}>{title}</ToastPrimitive.Title>}
        {description && (
          <ToastPrimitive.Description className={styles.description}>{description}</ToastPrimitive.Description>
        )}
        {children}
        {action && (
          <ToastPrimitive.Action className={styles.action} altText={action.label} onClick={action.handleClick}>
            {action.label}
          </ToastPrimitive.Action>
        )}
      </Flex>
      <ToastPrimitive.Close className={styles.close} data-testid={ElementIds.TOAST_CLOSE_BUTTON}>
        <X size={16} />
      </ToastPrimitive.Close>
    </ToastPrimitive.Root>
  );
};

export const ToastProvider = ({ children }: PropsWithChildren): React.ReactElement => {
  return (
    <ToastPrimitive.Provider swipeDirection="right">
      {children}
      <ToastPrimitive.Viewport className={styles.viewport} />
    </ToastPrimitive.Provider>
  );
};
