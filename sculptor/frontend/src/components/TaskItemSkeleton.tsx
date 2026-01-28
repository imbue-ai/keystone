import { Box, Flex, Skeleton } from "@radix-ui/themes";
import type { ReactElement } from "react";

const TaskItemSkeleton = (): ReactElement => {
  return (
    <Flex direction="column" gapY="2" overflow="hidden" flexShrink="0">
      <Flex direction="row" gapX="2" overflow="hidden" flexShrink="0">
        <Skeleton>
          <Box width="21px" height="21px" flexShrink="0" />
        </Skeleton>
        <Skeleton>
          <Box height="21px" width="2410px" />
        </Skeleton>
      </Flex>
      <Skeleton>
        <Box height="21px" width="2700px" />
      </Skeleton>
    </Flex>
  );
};

export const TaskListSkeleton = (): ReactElement => {
  return (
    <Flex direction="column" overflow="hidden" gapY="5" px="5" py="3">
      {Array.from({ length: 20 }).map((_, i) => (
        <TaskItemSkeleton key={i} />
      ))}
    </Flex>
  );
};
