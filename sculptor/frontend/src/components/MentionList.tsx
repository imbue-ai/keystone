import { Text } from "@radix-ui/themes";
import type { SuggestionProps } from "@tiptap/suggestion";
import classnames from "classnames";
import { forwardRef, useEffect, useImperativeHandle, useState } from "react";

import styles from "./MentionList.module.scss";

type MentionListRef = {
  onKeyDown: (props: { event: KeyboardEvent }) => boolean;
};

export const MentionList = forwardRef<MentionListRef, SuggestionProps>((props, ref) => {
  const [selectedIndex, setSelectedIndex] = useState(0);

  const selectItem = (index: number): void => {
    const item = props.items[index];

    if (item) {
      props.command(item);
    }
  };

  const upHandler = (): void => {
    setSelectedIndex((selectedIndex + props.items.length - 1) % props.items.length);
  };

  const downHandler = (): void => {
    setSelectedIndex((selectedIndex + 1) % props.items.length);
  };

  const enterHandler = (): void => {
    selectItem(selectedIndex);
  };

  useEffect(() => setSelectedIndex(0), [props.items]);

  useImperativeHandle(ref, () => ({
    onKeyDown: ({ event }): boolean => {
      if (event.key === "ArrowUp") {
        upHandler();
        return true;
      }

      if (event.key === "ArrowDown") {
        downHandler();
        return true;
      }

      if (event.key === "Enter") {
        enterHandler();
        return true;
      }

      return false;
    },
  }));

  return (
    <div className={styles.mentionList}>
      {props.items.length ? (
        props.items.map((item, index) => (
          <button
            className={classnames(styles.item, index === selectedIndex && styles.selected)}
            key={index}
            onClick={() => selectItem(index)}
          >
            <Text as="div" size="1">
              {item.label}
            </Text>
          </button>
        ))
      ) : (
        <Text as="div" size="1" className={styles.item}>
          No results
        </Text>
      )}
    </div>
  );
});
