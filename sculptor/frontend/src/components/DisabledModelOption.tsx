import { Tooltip } from "@radix-ui/themes";
import type { ReactElement } from "react";

import styles from "./DisabledModelOption.module.scss";

type DisabledModelOptionProps = {
  modelName: string;
  tooltipMessage: string;
};

/**
 * Renders a disabled model option with tooltip for model selectors
 */
export const DisabledModelOption = ({ modelName, tooltipMessage }: DisabledModelOptionProps): ReactElement => {
  return (
    <Tooltip content={tooltipMessage}>
      <div className={styles.disabledOption} data-disabled>
        {modelName}
      </div>
    </Tooltip>
  );
};
