import { useAtomValue } from "jotai";
import { useEffect, useState } from "react";
import { typeid } from "typeid-js";

import { appThemeAtom } from "./state/atoms/userConfig.ts";

/**
 * Runtime type assertion utility
 * @throws {Error} when assertion fails
 */
export function assertType<T>(
  value: unknown,
  check: (value: unknown) => value is T,
  message: string = "Type assertion failed",
): asserts value is T {
  if (!check(value)) {
    throw new Error(message);
  }
}

export const mergeClasses = (...classes: ReadonlyArray<string | undefined>): string => {
  return classes.filter((c) => c).join(" ");
};

export const optional = <T>(condition: boolean, value: T): T | undefined => {
  return condition ? value : undefined;
};

export const neutral = "gray" as const;

export const accent = "blue" as const;

export const clickable = "clickable" as const;

export const iconSize = {
  xs: "icon-xs",
  sm: "icon-sm",
  md: "icon-md",
  lg: "icon-lg",
  xl: "icon-xl",
};

export const colors = {
  accent: "accent",
  neutral: "neutral",
  success: "success",
  error: "error",
  warning: "warning",
  ignored: "ignored",
  nit: "nit",
};

export const replaceAlert = (linearId?: string): void => {
  const message = "Aha! If you're clicked this button you're now officially assigned to wire up this button!";
  if (linearId) {
    alert(`${message} Linear ID: ${linearId}`);
  } else {
    alert(message);
  }
};

export type CreatedAt = {
  time: number;
  tzaware: boolean;
};

export const toCreatedAt = (date: Date): CreatedAt => {
  return {
    time: date.getTime(),
    tzaware: date.getTimezoneOffset() !== 0,
  };
};

type Props = Record<string, unknown>;

export const getDataAttributesFromProps = <T>(props: T): { dataAttributes: Props; rest: T } => {
  const dataAttributes: Props = {};
  const rest: Props = {};
  const coercedProps = props as Props;

  Object.keys(coercedProps as Props).forEach((key) => {
    if (key.startsWith("data-")) {
      dataAttributes[key] = coercedProps[key];
    } else {
      rest[key] = coercedProps[key];
    }
  });

  return { dataAttributes, rest: rest as T };
};

export const makeRequestId = (): string => {
  return typeid("rqst").toString();
};

type Theme = "light" | "dark";

export const useResolvedTheme = (): Theme => {
  const configTheme = useAtomValue(appThemeAtom);
  const [systemTheme, setSystemTheme] = useState<Theme>("light");

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) {
      return;
    }

    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");

    const updateSystemTheme = (): void => {
      setSystemTheme(mediaQuery.matches ? "dark" : "light");
    };

    // Set initial system theme
    updateSystemTheme();

    // Listen for system theme changes
    mediaQuery.addEventListener("change", updateSystemTheme);

    return (): void => mediaQuery.removeEventListener("change", updateSystemTheme);
  }, []);

  // Resolve theme based on user preference
  if (configTheme === "system") {
    return systemTheme;
  }

  return (configTheme as Theme) || "light";
};
