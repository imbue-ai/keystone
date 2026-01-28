import { useAtom } from "jotai";
import type { MutableRefObject } from "react";
import { useEffect, useLayoutEffect, useRef } from "react";

import { chatScrollPositionAtomFamily } from "~/common/state/atoms/tasks";
import type { TaskID } from "~/common/Types";

export const useScrollPersistence = (taskID: TaskID): MutableRefObject<HTMLDivElement | null> => {
  const [scrollPosition, setScrollPosition] = useAtom(chatScrollPositionAtomFamily(taskID));
  const scrollAreaRef = useRef<HTMLDivElement | null>(null);

  // Save scroll position when user scrolls
  useEffect(() => {
    const scrollElement = scrollAreaRef.current;
    if (!scrollElement) {
      return;
    }

    const handleScroll = (): void => {
      setScrollPosition(scrollElement.scrollTop);
    };

    scrollElement.addEventListener("scroll", handleScroll, { passive: true });
    return (): void => {
      scrollElement.removeEventListener("scroll", handleScroll);
    };
  }, [setScrollPosition, taskID]);

  // Restore scroll position when switching tasks
  useLayoutEffect(() => {
    const scrollElement = scrollAreaRef.current;
    if (!scrollElement) {
      return;
    }

    if (scrollPosition !== null) {
      scrollElement.scrollTop = scrollPosition;
    } else {
      // If no saved scroll position, scroll to bottom
      scrollElement.scrollTop = scrollElement.scrollHeight;
    }

    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskID]);

  return scrollAreaRef;
};
