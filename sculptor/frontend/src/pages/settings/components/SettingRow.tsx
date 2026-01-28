import { Flex, Text } from "@radix-ui/themes";
import type { ReactElement, ReactNode } from "react";

import styles from "./SettingRow.module.scss";

type SettingRowProps = {
  title: string;
  description: string;
  children: ReactNode;
  footer?: ReactNode | undefined;
};

export const SettingRow = ({ title, description, children, footer }: SettingRowProps): ReactElement => (
  <Flex direction="column" width="100%" py="4" className={styles.settingRow}>
    <Text weight="medium">{title}</Text>
    <Flex justify="between" align="center" gapX="7" gapY="3">
      <Text size="2" className={styles.descriptionText}>
        {description}
      </Text>
      {children}
    </Flex>
    {footer}
  </Flex>
);
