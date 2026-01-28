import { Box, Button, Dialog, Flex, ScrollArea, Text } from "@radix-ui/themes";
import { type ReactElement, useState } from "react";

import styles from "./ToolBlock.module.scss";

type ToolBlockProps = {
  invocationString: string;
  content: string;
  isError?: boolean;
};

export const ToolBlock = ({ invocationString, content, isError = false }: ToolBlockProps): ReactElement => {
  return (
    <Flex direction="column">
      <Box px="2" py="1" className={styles.header}>
        <Text>{invocationString}</Text>
      </Box>
      {isError ? (
        <Box px="2" py="1" className={styles.error}>
          <Text>Error: {content}</Text>
        </Box>
      ) : (
        <Box className={styles.body} maxHeight="200px">
          <ScrollArea className={styles.scrollArea} scrollbars="vertical">
            <Box px="2" py="1">
              <Text>{content}</Text>
            </Box>
          </ScrollArea>
        </Box>
      )}
    </Flex>
  );
};

export const ToolBlockHTML = ({ invocationString, content }: ToolBlockProps): ReactElement => {
  // Display tool block with button to open a modal with the HTML content
  // The HTML is displayed in an iframe
  const [isModalOpen, setIsModalOpen] = useState(false);
  return (
    <Flex direction="column">
      <Box px="2" py="1" className={styles.header}>
        <Text>{invocationString}</Text>
      </Box>
      <Button onClick={() => setIsModalOpen(true)}>View Output</Button>
      {isModalOpen && (
        <Dialog.Root open={isModalOpen} onOpenChange={setIsModalOpen}>
          <Dialog.Content className={styles.htmlContentModal}>
            <Flex direction="column" height="100%" width="100%" className={styles.dialogContent} pl="4">
              <Dialog.Title>{invocationString}</Dialog.Title>
              <ScrollArea className={styles.contentScrollArea}>
                <Box px="2" py="1" className={styles.iframeContainer}>
                  <iframe
                    className={styles.htmlContentIframe}
                    src={`data:text/html;charset=utf-8,${encodeURIComponent(content)}`}
                    sandbox="allow-same-origin allow-scripts"
                  />
                </Box>
              </ScrollArea>
            </Flex>
          </Dialog.Content>
        </Dialog.Root>
      )}
    </Flex>
  );
};
