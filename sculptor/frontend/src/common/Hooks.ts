import { useSetAtom } from "jotai";
import type { DebouncedFunc } from "lodash";
import { debounce } from "lodash";
import type { MutableRefObject, RefObject } from "react";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { componentWidthAtomFamily } from "./state/atoms/responsiveLayout.ts";

type HoverHookType<T> = {
  hoverRef: MutableRefObject<T | undefined>;
  isHovered: boolean;
};

export const useHoverWithRef = <T extends HTMLElement = HTMLElement>(): HoverHookType<T> => {
  const [isHovered, setIsHovered] = useState(false);
  const ref = useRef<T>();

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const handleMouseEnter = (): void => {
      setIsHovered(true);
    };

    const handleMouseLeave = (): void => {
      setIsHovered(false);
    };

    element.addEventListener("mouseenter", handleMouseEnter);
    element.addEventListener("mouseleave", handleMouseLeave);

    return (): void => {
      element.removeEventListener("mouseenter", handleMouseEnter);
      element.removeEventListener("mouseleave", handleMouseLeave);
    };
  }, []);

  return { hoverRef: ref, isHovered };
};
type DraggableHookOutput<T> = {
  draggableRef: MutableRefObject<T | undefined>;
  pixelDelta: number;
  dragX: number;
  dragY: number;
  initialX: number;
  initialY: number;
  relativeX: number;
  relativeY: number;
  isHovered: boolean;
  isDragging: boolean;
};
export const useDraggable = <T extends HTMLElement = HTMLElement>(
  minWidth: number = 0,
  minHeight: number = 0,
): DraggableHookOutput<T> => {
  const [isDragging, setIsDragging] = useState(false);
  const [initialX, setInitialX] = useState<number>(minWidth);
  const [initialY, setInitialY] = useState<number>(minHeight);
  const [dragX, setDragX] = useState<number>(minWidth);
  const [dragY, setDragY] = useState<number>(minHeight);
  const [offsetX, setOffsetX] = useState<number>(0);
  const [offsetY, setOffsetY] = useState<number>(0);
  const { hoverRef, isHovered } = useHoverWithRef<T>();

  useEffect(() => {
    const handleMouseUp = (): void => {
      setIsDragging(false);
    };

    const handleMouseDown = (event: MouseEvent): void => {
      // check if the target element in the event matches the target
      if (event.target !== hoverRef.current || !hoverRef.current || !(hoverRef.current instanceof HTMLElement)) {
        return;
      }
      event.preventDefault();
      setInitialX(event.clientX);
      setInitialY(event.clientY);
      setDragX(event.clientX);
      setDragY(event.clientY);
      setIsDragging(true);

      const elementRect = hoverRef.current.getBoundingClientRect();
      setOffsetX(event.clientX - elementRect.left);
      setOffsetY(event.clientY - elementRect.top);
    };

    // TODO: this event might be triggered more often than we want
    const handleMouseMove = (event: MouseEvent): void => {
      if (!isDragging) return;
      event.preventDefault();
      setDragX(event.clientX);
      setDragY(event.clientY);
    };

    document.addEventListener("mousedown", handleMouseDown);
    document.addEventListener("mouseup", handleMouseUp);
    document.addEventListener("mousemove", handleMouseMove);

    return (): void => {
      document.removeEventListener("mousedown", handleMouseDown);
      document.removeEventListener("mouseup", handleMouseUp);
      document.removeEventListener("mousemove", handleMouseMove);
    };
  });

  return {
    draggableRef: hoverRef,
    pixelDelta: dragX - initialX,
    dragX,
    dragY,
    initialX,
    initialY,
    relativeX: dragX - offsetX,
    relativeY: dragY - offsetY,
    isHovered,
    isDragging,
  };
};
type VoidFunction = () => void;
export const useDebounce = (delay: number, callback: VoidFunction): DebouncedFunc<never> => {
  const ref = useRef<VoidFunction | null>(null);

  useEffect(() => {
    ref.current = callback;
  }, [callback]);

  return useMemo<DebouncedFunc<never>>(() => {
    const func = (): void => {
      ref.current?.();
    };

    return debounce(func, delay);
  }, [delay]);
};

export type SetOperations<T> = {
  add: (...items: Array<T>) => void;
  remove: (...items: Array<T>) => void;
  has: (item: T) => boolean;
  toggle: (item: T) => void;
  clear: () => void;
  reset: (values: Iterable<T>) => void;
  size: number;
};

/**
 * A custom hook that provides Set functionality with React state
 * @param initialValues - The initial values for the Set
 * @returns A tuple containing the Set and operations to modify it
 */
export const useSet = <T>(initialValues: Iterable<T> = []): [Set<T>, SetOperations<T>] => {
  const [set, setSet] = useState<Set<T>>(new Set(initialValues));

  // Add one or more items to the set
  const add = useCallback((...items: Array<T>): void => {
    setSet((prevSet) => {
      const newSet = new Set(prevSet);
      items.forEach((item) => newSet.add(item));
      return newSet;
    });
  }, []);

  // Remove one or more items from the set
  const remove = useCallback((...items: Array<T>): void => {
    setSet((prevSet) => {
      const newSet = new Set(prevSet);
      items.forEach((item) => newSet.delete(item));
      return newSet;
    });
  }, []);

  // Check if the set contains an item
  const has = useCallback((item: T): boolean => set.has(item), [set]);

  // Toggle an item in the set (add if not present, remove if present)
  const toggle = useCallback((item: T): void => {
    setSet((prevSet) => {
      const newSet = new Set(prevSet);
      if (newSet.has(item)) {
        newSet.delete(item);
      } else {
        newSet.add(item);
      }
      return newSet;
    });
  }, []);

  // Clear all items from the set
  const clear = useCallback((): void => {
    setSet(new Set());
  }, []);

  // Reset to specific values
  const reset = useCallback((values: Iterable<T>): void => {
    setSet(new Set(values));
  }, []);

  // Return the set and operations
  return [
    set,
    {
      add,
      remove,
      has,
      toggle,
      clear,
      reset,
      size: set.size,
    },
  ];
};

export const useComponentWidth = (componentID?: string): { ref: RefObject<HTMLDivElement>; width: number } => {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState<number>(0);
  const setComponentWidth = useSetAtom(componentWidthAtomFamily(componentID));

  useEffect(() => {
    const element = ref.current;
    if (!element) return;

    const updateWidth = (): void => {
      const currentWidth = element.getBoundingClientRect().width;
      setWidth(currentWidth);
      if (componentID) {
        setComponentWidth(currentWidth);
      }
    };

    updateWidth();

    const resizeObserver = new ResizeObserver(updateWidth);
    resizeObserver.observe(element);

    return (): void => {
      if (element) resizeObserver.unobserve(element);
    };
  }, [componentID, setComponentWidth]);

  return { ref, width };
};

export const useHover = (): {
  isHovered: boolean;
  hoverProps: { onMouseEnter: () => void; onMouseLeave: () => void };
} => {
  const [isHovered, setIsHovered] = React.useState(false);

  const handleMouseEnter = (): void => setIsHovered(true);
  const handleMouseLeave = (): void => setIsHovered(false);

  const hoverProps = {
    onMouseEnter: handleMouseEnter,
    onMouseLeave: handleMouseLeave,
  };

  return { isHovered, hoverProps };
};
