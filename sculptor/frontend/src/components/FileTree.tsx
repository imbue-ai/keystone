import { Box, Flex, Text } from "@radix-ui/themes";
import { FileIcon, FolderClosedIcon, FolderOpenIcon } from "lucide-react";
import type { ReactElement } from "react";
import { useMemo, useState } from "react";

import styles from "./FileTree.module.scss";

type TreeNode = {
  name: string;
  path: string;
  isFolder: boolean;
  children: Array<TreeNode>;
};

const buildTree = (paths: Array<string>): Array<TreeNode> => {
  const nodes: Record<string, TreeNode> = {};

  const getNode = (fullPath: string): TreeNode => {
    if (!nodes[fullPath]) {
      nodes[fullPath] = {
        name: fullPath.split("/").pop() || fullPath,
        path: fullPath,
        isFolder: false,
        children: [],
      };
    }
    return nodes[fullPath];
  };

  paths.forEach((filePath: string): void => {
    const parts = filePath.split("/");
    let accum = "";

    for (let i = 0; i < parts.length; i++) {
      accum = i === 0 ? parts[i] : `${accum}/${parts[i]}`;
      const node = getNode(accum);
      if (i < parts.length - 1) {
        node.isFolder = true;
      }

      if (i > 0) {
        const parentPath = parts.slice(0, i).join("/");
        const parentNode = getNode(parentPath);

        if (!parentNode.children.some((c) => c.path === node.path)) {
          parentNode.children.push(node);
        }
      }
    }
  });

  return Object.values(nodes).filter((n) => !n.path.includes("/"));
};

const renderTree = (
  nodes: Array<TreeNode>,
  closedFolders: Set<string>,
  toggleFolder: (path: string) => void,
  onFileSelect: (path: string) => void,
  selectedPath: string | null,
  level = 0,
): Array<ReactElement> => {
  nodes.sort((a, b) => Number(b.isFolder) - Number(a.isFolder));

  return nodes.map((node) => {
    const isOpen = !closedFolders.has(node.path);
    const icon = node.isFolder ? (
      isOpen ? (
        <FolderOpenIcon size="16px" />
      ) : (
        <FolderClosedIcon size="16px" />
      )
    ) : (
      <FileIcon size="16px" />
    );

    const isSelected = !node.isFolder && node.path === selectedPath;

    const row = (
      <Flex
        key={node.path}
        align="center"
        gapX="1"
        className={`${styles.treeRow} ${isSelected ? styles.selectedTreeRow : ""}`}
        style={{
          cursor: "pointer",
          paddingLeft: level * 16 + 16,
        }}
        onClick={() => {
          if (node.isFolder) {
            toggleFolder(node.path);
          } else {
            onFileSelect(node.path);
          }
        }}
      >
        {icon}
        <Text truncate={true} className={`${node.isFolder ? styles.folderText : styles.fileText}`}>
          {node.name}
        </Text>
      </Flex>
    );

    const children = isOpen
      ? renderTree(node.children, closedFolders, toggleFolder, onFileSelect, selectedPath, level + 1)
      : null;

    return (
      <Box key={node.path}>
        {row}
        {children}
      </Box>
    );
  });
};

type FileTreeProps = {
  changedFiles: Array<string>;
  onFileSelect: (file: string) => void;
  activeFile: string | null;
};

export const FileTree = ({ changedFiles, onFileSelect, activeFile }: FileTreeProps): ReactElement => {
  const [closedFolders, setClosedFolders] = useState<Set<string>>(new Set());

  const treeData = useMemo(() => buildTree(changedFiles), [changedFiles]);

  const toggleFolder = (path: string): void => {
    setClosedFolders((prev) => {
      const copy = new Set(prev);

      if (copy.has(path)) {
        copy.delete(path);
      } else {
        copy.add(path);
      }

      return copy;
    });
  };

  const handleFileSelect = (path: string): void => {
    onFileSelect(path);
  };

  return (
    <Box className={styles.fileTreePane} pr="3">
      <Flex direction="column" gap="1">
        {renderTree(treeData, closedFolders, toggleFolder, handleFileSelect, activeFile)}
      </Flex>
    </Box>
  );
};
