import { Flex, Text } from "@radix-ui/themes";
import { GitBranchIcon } from "lucide-react";
import type { ReactElement } from "react";
import { memo, useEffect, useMemo, useState } from "react";

import type { RepoInfo } from "~/api";
import { ElementIds } from "~/api";
import { BranchSelectorCore, type BranchWithBadges } from "~/components/BranchSelectorCore.tsx";

import styles from "./BranchSelector.module.scss";

type BranchSelectorProps = {
  repoInfo: RepoInfo | null;
  fetchRepoInfo: () => Promise<RepoInfo | undefined>;
  sourceBranch: string | undefined;
  setUserSelectedBranch: (branch: string) => void;
};

const BranchSelectorComponent = ({
  repoInfo,
  fetchRepoInfo,
  sourceBranch,
  setUserSelectedBranch,
}: BranchSelectorProps): ReactElement => {
  const [shouldFetch, setShouldFetch] = useState(false);
  const [isFetchingBranches, setIsFetchingBranches] = useState(false);

  const selectedBranchName = sourceBranch?.replace(/\*$/, "") || "";
  const areBranchesLoaded = (repoInfo?.recentBranches?.length ?? 0) > 0;

  const branches: Array<BranchWithBadges> = useMemo(() => {
    const branchOptions = repoInfo?.recentBranches || [];
    const numUncommittedChanges = repoInfo?.numUncommittedChanges ?? 0;
    const hasUncommittedChanges = numUncommittedChanges > 0;

    return branchOptions.map((branch) => {
      const isCurrentBranch = branch === repoInfo?.currentBranch;
      const badges: Array<string | { text: string; tooltip?: string }> = [];

      if (isCurrentBranch) {
        badges.push("current");
        if (hasUncommittedChanges) {
          badges.push({
            text: `+${numUncommittedChanges} uncommitted changes`,
            tooltip: "*Sculptor will include your local uncommitted changes",
          });
        }
      }

      return {
        branch: isCurrentBranch && hasUncommittedChanges ? `${branch}*` : branch,
        badges,
      };
    });
  }, [repoInfo]);

  const displayBranchName = useMemo(() => {
    const numUncommittedChanges = repoInfo?.numUncommittedChanges ?? 0;
    const hasUncommittedChanges = numUncommittedChanges > 0;
    const isCurrentBranch = selectedBranchName === repoInfo?.currentBranch;

    return isCurrentBranch && hasUncommittedChanges ? `${selectedBranchName}*` : selectedBranchName;
  }, [selectedBranchName, repoInfo]);

  useEffect(() => {
    if (shouldFetch && !isFetchingBranches) {
      setIsFetchingBranches(true);
      fetchRepoInfo().finally(() => {
        setShouldFetch(false);
        setIsFetchingBranches(false);
      });
    }
  }, [shouldFetch, fetchRepoInfo, isFetchingBranches]);

  return (
    <BranchSelectorCore
      selectedBranch={selectedBranchName}
      onBranchSelected={(branch) => {
        setUserSelectedBranch(branch);
        setShouldFetch(true);
      }}
      branches={branches}
      isLoadingBranches={!areBranchesLoaded && isFetchingBranches}
      triggerContent={
        <Flex align="center" gapX="2" className={styles.dropdownButton}>
          <GitBranchIcon />
          <Text className={styles.branchName} truncate={true}>
            {displayBranchName}
          </Text>
        </Flex>
      }
      testId={ElementIds.BRANCH_SELECTOR}
      className={styles.dropdownButton}
      onOpenChange={(open) => open && setShouldFetch(true)}
    />
  );
};

export const BranchSelector = memo(BranchSelectorComponent);
