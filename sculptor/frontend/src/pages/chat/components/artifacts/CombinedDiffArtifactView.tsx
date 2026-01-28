import { Flex } from "@radix-ui/themes";
import { GitBranch } from "lucide-react";
import type { ReactElement } from "react";
import { useMemo } from "react";

import { ArtifactType } from "../../../../api";
import { CommittedDiffView, UncommittedDiffView } from "../../../../components/DiffView";
import type { ArtifactViewContentProps, ArtifactViewTabLabelProps } from "../../Types.ts";

export const CombinedDiffArtifactViewComponent = (props: ArtifactViewContentProps): ReactElement => {
  const diffArtifact = props.artifacts[ArtifactType.DIFF];

  return useMemo((): ReactElement => {
    return (
      <Flex direction="column" gapY="4">
        <UncommittedDiffView diff={diffArtifact?.uncommittedDiff} />
        <CommittedDiffView diff={diffArtifact?.committedDiff} />
      </Flex>
    );
  }, [diffArtifact]);
};

export const CombinedDiffTabLabelComponent = ({ shouldShowIcon = true }: ArtifactViewTabLabelProps): ReactElement => {
  return (
    <Flex align="center" gap="2">
      {shouldShowIcon && <GitBranch />}
      Changes
    </Flex>
  );
};
