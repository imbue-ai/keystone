import { Badge, Box, Flex, Spinner, Text } from "@radix-ui/themes";
import { CheckSquare } from "lucide-react";
import { Check } from "lucide-react";
import { type ReactElement, useMemo } from "react";

import { ArtifactType, ElementIds, type TodoItem, TodoStatus } from "../../../../api";
import { mergeClasses, optional } from "../../../../common/Utils";
import type { ArtifactViewContentProps, ArtifactViewTabLabelProps } from "../../Types.ts";
import styles from "./TodoListArtifactView.module.scss";

type TodoListViewProps = {
  todoList: Array<TodoItem> | null;
};

const TodoListView = ({ todoList }: TodoListViewProps): ReactElement => {
  if (!todoList || todoList.length === 0) {
    return (
      <Flex className={styles.noTodoList} justify="center" align="center" p="3">
        <Text color="gray">No plan yet</Text>
      </Flex>
    );
  }

  const completedCount = todoList.filter((t) => t.status === TodoStatus.COMPLETED).length;
  const inProgressCount = todoList.filter((t) => t.status === TodoStatus.IN_PROGRESS).length;
  const totalCount = todoList.length;

  return (
    <Flex direction="column" className={styles.todoListContainer} gapY="3">
      <Flex direction="column" gapY="3" className={styles.todoList}>
        {todoList.map((todo, index) => (
          <TodoItemComponent key={todo.id} todo={todo} itemNumber={index + 1} />
        ))}
      </Flex>

      <Flex align="center" gap="2" className={styles.progressIndicator} p="3">
        {completedCount === totalCount ? <Check size={16} className={styles.progressIcon} /> : <Spinner size="1" />}
        <Text size="2" color="gray">
          {completedCount} of {totalCount} Done
        </Text>
        {inProgressCount > 0 && (
          <Text size="2" color="gray" className={styles.ghostText}>
            Working on Todo #{todoList.findIndex((t) => t.status === TodoStatus.IN_PROGRESS) + 1}
          </Text>
        )}
      </Flex>
    </Flex>
  );
};

type TodoItemProps = {
  todo: TodoItem;
  itemNumber: number;
};

const TodoItemComponent = ({ todo, itemNumber }: TodoItemProps): ReactElement => {
  const isCompleted = todo.status === TodoStatus.COMPLETED;
  const isInProgress = todo.status === TodoStatus.IN_PROGRESS;

  return (
    <Flex
      align="center"
      gap="3"
      pl="8px"
      className={mergeClasses(styles.todoItem, optional(isInProgress, styles.inProgress))}
      data-testid={ElementIds.ARTIFACT_PLAN_ITEM}
    >
      <Box className={styles.statusIndicator}>
        {isCompleted && <Check className={styles.checkIcon} data-testid={ElementIds.ARTIFACT_PLAN_CHECKMARK} />}
        {!isCompleted && isInProgress && <Spinner size="1" />}
        {!isCompleted && !isInProgress && (
          <Text size="2" color="gray">
            {itemNumber}.
          </Text>
        )}
      </Box>
      <Text size="2" className={mergeClasses(styles.todoText, optional(isCompleted, styles.completed))} truncate={true}>
        {todo.content}
      </Text>

      <Badge color="gold" variant="soft" size="1" className={styles.priorityBadge}>
        {todo.priority}
      </Badge>
    </Flex>
  );
};

export const TodoListArtifactViewComponent = ({ artifacts }: ArtifactViewContentProps): ReactElement => {
  const todoListArtifact = artifacts[ArtifactType.PLAN];

  // Only re-render when the todo list data changes
  const component = useMemo((): ReactElement => {
    const todoListData = todoListArtifact?.todos || null;
    return <TodoListView todoList={todoListData} />;
  }, [todoListArtifact]);

  return component;
};

export const TodoListTabLabelComponent = ({ shouldShowIcon = true }: ArtifactViewTabLabelProps): ReactElement => {
  return (
    <Flex align="center" gap="2">
      {shouldShowIcon && <CheckSquare />}
      Plan
    </Flex>
  );
};
