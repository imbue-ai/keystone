import { Text } from "@radix-ui/themes";
import { useAtomValue } from "jotai";
import type { ReactElement } from "react";

import { ElementIds } from "~/api";
import { healthCheckDataAtom } from "~/common/state/atoms/backend.ts";

export const VersionDisplay = (): ReactElement => {
  const healthCheckData = useAtomValue(healthCheckDataAtom);

  return (
    <Text color="gold" data-testid={ElementIds.VERSION}>
      {healthCheckData?.version}
    </Text>
  );
};
