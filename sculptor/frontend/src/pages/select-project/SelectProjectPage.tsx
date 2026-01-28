import { AlertDialog, Box, Button, Flex, Spinner, Text, TextField } from "@radix-ui/themes";
import { FolderGitIcon } from "lucide-react";
import type { ReactElement } from "react";
import { useEffect, useState } from "react";

import { HTTPException } from "~/common/Errors.ts";

import {
  createInitialCommit,
  ElementIds,
  getActiveProjects,
  getMostRecentlyUsedProject,
  initializeGitRepository,
  initializeProject,
} from "../../api";
import { useImbueNavigate } from "../../common/NavigateUtils.ts";
import { TitleBar } from "../../components/TitleBar.tsx";
import { isElectron, selectProjectDirectory } from "../../electron/utils";
import styles from "./SelectProjectPage.module.scss";

export const SelectProjectPage = (): ReactElement => {
  const [projectPath, setProjectPath] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isCheckingProjects, setIsCheckingProjects] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [shouldShowGitInitDialog, setShouldShowGitInitDialog] = useState(false);
  const [shouldShowInitialCommitDialog, setShouldShowInitialCommitDialog] = useState(false);
  const [pendingPath, setPendingPath] = useState("");
  const { navigateToHome } = useImbueNavigate();

  // Check for active projects and auto-navigate to most recent
  useEffect((): void => {
    const checkProjects = async (): Promise<void> => {
      try {
        try {
          const { data: projectId } = await getMostRecentlyUsedProject({ meta: { skipWsAck: true } });
          if (projectId) {
            navigateToHome(projectId);
            return;
          }
        } catch (error) {
          console.error("Failed to check and navigate to most recently used project:", error);
        }

        // Fallback: check for any active projects
        try {
          const { data: projects } = await getActiveProjects();
          if (projects && projects.length > 0) {
            const mostRecent = projects[0];
            navigateToHome(mostRecent.objectId);
          }
        } catch (error) {
          console.error("Failed to check and navigate to most recent active project:", error);
        }
      } finally {
        setIsCheckingProjects(false);
      }
    };

    void checkProjects();
  }, [navigateToHome]);

  const handleSelectDirectory = async (): Promise<void> => {
    if (isElectron()) {
      try {
        const path = await selectProjectDirectory();
        if (path) {
          setProjectPath(path);
          setError(null);
          await handleSubmit(path);
        }
      } catch (error) {
        console.error("Failed to select directory:", error);
        setError("Failed to open directory selector");
      }
    }
  };

  const handleSubmit = async (path = projectPath): Promise<void> => {
    if (!path) {
      setError("Please select a project directory");
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const { data: project } = await initializeProject({
        body: { projectPath: path },
        meta: { skipWsAck: true },
      });

      navigateToHome(project.objectId);
    } catch (error) {
      if (error instanceof HTTPException && error.status === 400 && error.detail.includes("not a git repository")) {
        setPendingPath(path);
        setShouldShowGitInitDialog(true);
      } else if (error instanceof HTTPException && error.status === 409 && error.detail.includes("initial commit")) {
        setPendingPath(path);
        setShouldShowInitialCommitDialog(true);
      } else {
        let errorMessage = "Failed to select project";
        if (error instanceof HTTPException) {
          errorMessage = error.detail;
        } else if (error instanceof Error) {
          errorMessage = error.message;
        }
        setError(errorMessage);
      }
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent): void => {
    if (e.key === "Enter") {
      handleSubmit();
    }
  };

  const handleGitInit = async (): Promise<void> => {
    setShouldShowGitInitDialog(false);
    setIsLoading(true);
    setError(null);

    try {
      await initializeGitRepository({
        body: { projectPath: pendingPath },
        meta: { skipWsAck: true },
      });
      // Now try initializing the project
      const { data: project } = await initializeProject({
        body: { projectPath: pendingPath },
        meta: { skipWsAck: true },
      });

      navigateToHome(project.objectId);
    } catch (initError) {
      let errorMessage = "Failed to initialize git repository";
      if (initError instanceof HTTPException) {
        errorMessage = initError.detail;
      } else if (initError instanceof Error) {
        errorMessage = initError.message;
      }
      setError(errorMessage);
    } finally {
      setIsLoading(false);
    }
  };

  const handleCancelGitInit = (): void => {
    setShouldShowGitInitDialog(false);
    setError("Please select a directory that is a git repository");
  };

  const handleInitialCommit = async (): Promise<void> => {
    setShouldShowInitialCommitDialog(false);
    setIsLoading(true);

    try {
      await createInitialCommit({
        body: { projectPath: pendingPath },
        meta: { skipWsAck: true },
      });

      const { data: project } = await initializeProject({
        body: { projectPath: pendingPath },
      });

      navigateToHome(project.objectId);
    } catch (initError) {
      let errorMessage = "Failed to create initial commit";
      if (initError instanceof HTTPException) {
        errorMessage = initError.detail;
      } else if (initError instanceof Error) {
        errorMessage = initError.message;
      }
      setError(errorMessage);
    } finally {
      setIsLoading(false);
    }
  };

  const handleCancelInitialCommit = (): void => {
    setShouldShowInitialCommitDialog(false);
  };

  if (isCheckingProjects) {
    return (
      <Flex align="center" justify="center" className={styles.container}>
        <Spinner size="3" />
      </Flex>
    );
  }

  return (
    <>
      <AlertDialog.Root open={shouldShowGitInitDialog} onOpenChange={setShouldShowGitInitDialog}>
        <AlertDialog.Content maxWidth="450px" data-testid={ElementIds.PROJECT_GIT_INIT_DIALOG}>
          <AlertDialog.Title>Initialize Git Repository</AlertDialog.Title>
          <AlertDialog.Description size="2">
            This directory is not a git repository. Would you like to initialize it as one?
          </AlertDialog.Description>
          <Flex gap="3" mt="4" justify="end">
            <AlertDialog.Cancel>
              <Button
                variant="soft"
                color="gray"
                onClick={handleCancelGitInit}
                data-testid={ElementIds.PROJECT_GIT_INIT_CANCEL}
              >
                Cancel
              </Button>
            </AlertDialog.Cancel>
            <AlertDialog.Action>
              <Button variant="solid" onClick={handleGitInit} data-testid={ElementIds.PROJECT_GIT_INIT_CONFIRM}>
                Initialize Git
              </Button>
            </AlertDialog.Action>
          </Flex>
        </AlertDialog.Content>
      </AlertDialog.Root>

      {/* Initial Commit Dialog */}
      <AlertDialog.Root open={shouldShowInitialCommitDialog} onOpenChange={setShouldShowInitialCommitDialog}>
        <AlertDialog.Content maxWidth="450px" data-testid={ElementIds.PROJECT_INITIAL_COMMIT_DIALOG}>
          <AlertDialog.Title>Make Initial Commit</AlertDialog.Title>
          <AlertDialog.Description size="2">
            The git repository is empty. Would you like to make an initial commit?
          </AlertDialog.Description>
          <Flex gap="3" mt="4" justify="end">
            <AlertDialog.Cancel>
              <Button
                variant="soft"
                color="gray"
                onClick={handleCancelInitialCommit}
                data-testid={ElementIds.PROJECT_INITIAL_COMMIT_CANCEL}
              >
                Cancel
              </Button>
            </AlertDialog.Cancel>
            <AlertDialog.Action>
              <Button
                variant="solid"
                onClick={handleInitialCommit}
                disabled={isLoading}
                data-testid={ElementIds.PROJECT_INITIAL_COMMIT_CONFIRM}
              >
                {isLoading ? <Spinner /> : "Make Initial Commit"}
              </Button>
            </AlertDialog.Action>
          </Flex>
        </AlertDialog.Content>
      </AlertDialog.Root>

      <Flex align="center" justify="center" className={styles.container} data-testid={ElementIds.SELECT_PROJECT_PAGE}>
        <TitleBar />
        <Box width="400px">
          <Flex direction="column" gap="4" p="5">
            <Text className={styles.primaryText}>Open Project</Text>

            <Flex direction="column" gap="2">
              <TextField.Root
                placeholder="/path/to/repo"
                value={projectPath}
                onChange={(e) => setProjectPath(e.target.value)}
                onKeyPress={handleKeyPress}
                className={styles.pathInput}
                data-testid={ElementIds.PROJECT_PATH_INPUT}
              />

              {error && (
                <Text size="2" color="red" className={styles.error} data-testid={ElementIds.PROJECT_SELECTOR_ERROR}>
                  {error}
                </Text>
              )}
            </Flex>

            <Button
              size="3"
              variant="solid"
              onClick={isElectron() && !projectPath ? handleSelectDirectory : (): Promise<void> => handleSubmit()}
              disabled={isLoading}
              className={styles.selectButton}
              data-testid={ElementIds.PROJECT_SELECT_BUTTON}
            >
              {isLoading ? (
                <Spinner />
              ) : (
                <Flex gap="2" align="center">
                  <FolderGitIcon />
                  <Text>Select Repo</Text>
                </Flex>
              )}
            </Button>
          </Flex>
        </Box>
      </Flex>
    </>
  );
};
