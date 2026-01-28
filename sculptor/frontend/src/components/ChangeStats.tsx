import { Flex, Heading, Text } from "@radix-ui/themes";
import type { ReactElement } from "react";

import { mergeClasses, optional } from "../common/Utils.ts";
import styles from "./ChangeStats.module.scss";
import type { ChangeStatsType } from "./DiffUtils.ts";

export const ChangeStats = (
  props: ChangeStatsType & {
    icon?: ReactElement;
  },
): ReactElement => {
  const isDiffEmpty = props.filesChanged === 0 && props.added === 0 && props.removed === 0;

  return (
    <Flex align="center" gap="2" className={mergeClasses(optional(isDiffEmpty, styles.empty))}>
      {props.icon}
      <Heading size="2" weight="medium" className={mergeClasses(optional(isDiffEmpty, styles.empty), styles.stats)}>
        {isDiffEmpty ? "No files changed" : <>{props.filesChanged} files changed</>}
      </Heading>
      {!isDiffEmpty && (
        <Flex gap="2" pl="2">
          <Text className={styles.linesAdded}>+{props.added}</Text>
          <Text className={styles.linesRemoved}>-{props.removed}</Text>
        </Flex>
      )}
    </Flex>
  );
};
