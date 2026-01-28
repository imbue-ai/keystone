import { AlertDialog, Button, Flex, Link, TextField } from "@radix-ui/themes";
import { useSetAtom } from "jotai";
import type { ReactElement } from "react";
import { useCallback, useEffect, useState } from "react";

import { HTTPException } from "~/common/Errors.ts";

import {
  createInitialCommit,
  ElementIds,
  initializeGitRepository,
  initializeProject,
  listProjects,
} from "../api/index.ts";
import { useImbueNavigate } from "../common/NavigateUtils.ts";
import { updateProjectsAtom } from "../common/state/atoms/projects.ts";
import { isElectron, selectProjectDirectory } from "../electron/utils.ts";
import type { ToastContent } from "./Toast.tsx";
import { ToastType } from "./Toast.tsx";

type AddProjectDialogProps = {
  setToast: (toast: ToastContent | null) => void;
  setShouldShowAddProjectDialog: (shouldShow: boolean) => void;
};

export const AddProjectDialog = ({
  setToast: setToast,
  setShouldShowAddProjectDialog: setShouldShowAddProjectDialog,
}: AddProjectDialogProps): ReactElement => {
  const [shouldShowPathModal, setShouldShowPathModal] = useState(false);
  const [shouldShowGitInitDialog, setShouldShowGitInitDialog] = useState(false);
  const [shouldShowInitialCommitDialog, setShouldShowInitialCommitDialog] = useState(false);
  const [pendingPath, setPendingPath] = useState("");
  const { navigateToHome } = useImbueNavigate();
  const updateProjects = useSetAtom(updateProjectsAtom);

  const handleOpenNewRepo = useCallback(
    async (path: string): Promise<void> => {
      setShouldShowPathModal(false);

      try {
        const { data: project } = await initializeProject({
          body: { projectPath: path },
        });

        // Get the updated project list
        const { data: projects } = await listProjects();
        updateProjects(projects);

        navigateToHome(project.objectId);
        setShouldShowAddProjectDialog(false);
      } catch (error) {
        if (error instanceof HTTPException && error.status === 400 && error.detail.includes("not a git repository")) {
          setPendingPath(path);
          setShouldShowGitInitDialog(true);
        } else if (error instanceof HTTPException && error.status === 409 && error.detail.includes("initial commit")) {
          setPendingPath(path);
          setShouldShowInitialCommitDialog(true);
        } else {
          let errorMessage = "Failed to open project";
          if (error instanceof HTTPException) {
            errorMessage = error.detail;
          } else if (error instanceof Error) {
            errorMessage = error.message;
          }
          setToast({ title: errorMessage, type: ToastType.ERROR });
          setShouldShowAddProjectDialog(false);
        }
      }
    },
    [navigateToHome, updateProjects, setToast, setShouldShowAddProjectDialog],
  );

  useEffect(() => {
    if (isElectron()) {
      try {
        selectProjectDirectory().then(async (path) => {
          if (path) {
            await handleOpenNewRepo(path);
          } else {
            // User cancelled the directory picker
            setShouldShowAddProjectDialog(false);
          }
        });
      } catch (error) {
        console.error("Failed to select directory:", error);
        setToast({ title: "Failed to select directory", type: ToastType.ERROR });
        setShouldShowAddProjectDialog(false);
      }
    } else {
      setShouldShowPathModal(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <>
      {/* Path Input Modal for non-Electron environments */}
      <SelectRepoPathDialog
        shouldShowPathModal={shouldShowPathModal}
        setShouldShowPathModal={setShouldShowPathModal}
        handleOpenNewRepo={handleOpenNewRepo}
        setShouldShowAddProjectDialog={setShouldShowAddProjectDialog}
      />

      {/* Git Init Dialog */}
      <GitInitDialog
        shouldShowGitInitDialog={shouldShowGitInitDialog}
        setShouldShowGitInitDialog={setShouldShowGitInitDialog}
        pendingPath={pendingPath}
        setToast={setToast}
        setShouldShowAddProjectDialog={setShouldShowAddProjectDialog}
      />

      {/* Initial Commit Dialog */}
      <InitialCommitDialog
        shouldShowInitialCommitDialog={shouldShowInitialCommitDialog}
        setShouldShowInitialCommitDialog={setShouldShowInitialCommitDialog}
        pendingPath={pendingPath}
        setToast={setToast}
        setShouldShowAddProjectDialog={setShouldShowAddProjectDialog}
      />
    </>
  );
};

type SelectRepoPathDialogProps = {
  shouldShowPathModal: boolean;
  setShouldShowPathModal: (shouldShow: boolean) => void;
  handleOpenNewRepo: (path: string) => void;
  setShouldShowAddProjectDialog: (shouldShow: boolean) => void;
};

const SelectRepoPathDialog = (props: SelectRepoPathDialogProps): ReactElement => {
  const [modalProjectPath, setModalProjectPath] = useState("");
  const { shouldShowPathModal, setShouldShowPathModal, handleOpenNewRepo, setShouldShowAddProjectDialog } = props;

  return (
    <AlertDialog.Root open={shouldShowPathModal} onOpenChange={setShouldShowPathModal}>
      <AlertDialog.Content maxWidth="450px" data-testid={ElementIds.OPEN_NEW_REPO_DIALOG}>
        <AlertDialog.Title>Open Repository</AlertDialog.Title>
        <AlertDialog.Description size="2">Enter the absolute path to your repository</AlertDialog.Description>
        <Flex direction="column" gap="3" mt="4">
          <TextField.Root
            placeholder="/path/to/your/repo"
            value={modalProjectPath}
            onChange={(e) => setModalProjectPath(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && modalProjectPath) {
                handleOpenNewRepo(modalProjectPath);
              }
            }}
            data-testid={ElementIds.OPEN_NEW_REPO_INPUT}
          />
        </Flex>
        <Flex gap="3" mt="4" justify="end">
          <AlertDialog.Cancel>
            <Button
              variant="soft"
              color="gray"
              data-testid={ElementIds.CANCEL_OPEN_NEW_REPO_BUTTON}
              onClick={() => setShouldShowAddProjectDialog(false)}
            >
              Cancel
            </Button>
          </AlertDialog.Cancel>
          <Button
            variant="solid"
            onClick={() => handleOpenNewRepo(modalProjectPath)}
            disabled={!modalProjectPath}
            data-testid={ElementIds.CONFIRM_OPEN_NEW_REPO_BUTTON}
          >
            Open Repository
          </Button>
        </Flex>
      </AlertDialog.Content>
    </AlertDialog.Root>
  );
};

type GitInitDialogProps = {
  shouldShowGitInitDialog: boolean;
  setShouldShowGitInitDialog: (shouldShow: boolean) => void;
  pendingPath: string;
  setToast: (toast: ToastContent | null) => void;
  setShouldShowAddProjectDialog: (shouldShow: boolean) => void;
};

const GitInitDialog = (props: GitInitDialogProps): ReactElement => {
  const { navigateToHome } = useImbueNavigate();
  const updateProjects = useSetAtom(updateProjectsAtom);
  const { shouldShowGitInitDialog, setShouldShowGitInitDialog, pendingPath, setToast, setShouldShowAddProjectDialog } =
    props;

  const handleGitInit = async (): Promise<void> => {
    setShouldShowGitInitDialog(false);
    setShouldShowAddProjectDialog(false);

    try {
      await initializeGitRepository({
        body: { projectPath: pendingPath },
        meta: { skipWsAck: true },
      });

      const { data: project } = await initializeProject({
        body: { projectPath: pendingPath },
      });

      // Get the updated project list
      const { data: projects } = await listProjects();
      updateProjects(projects);

      navigateToHome(project.objectId);
    } catch (initError) {
      let errorMessage = "Failed to initialize git repository";
      if (initError instanceof HTTPException) {
        errorMessage = initError.detail;
      } else if (initError instanceof Error) {
        errorMessage = initError.message;
      }
      setToast({ title: errorMessage, type: ToastType.ERROR });
    }
  };

  const handleCancelGitInit = (): void => {
    setShouldShowGitInitDialog(false);
    setShouldShowAddProjectDialog(false);
    setToast({ title: "Please select a directory that is a git repository", type: ToastType.ERROR });
  };
  return (
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
  );
};

type InitialCommitDialogProps = {
  shouldShowInitialCommitDialog: boolean;
  setShouldShowInitialCommitDialog: (shouldShow: boolean) => void;
  pendingPath: string;
  setToast: (toast: ToastContent | null) => void;
  setShouldShowAddProjectDialog: (shouldShow: boolean) => void;
};

const InitialCommitDialog = (props: InitialCommitDialogProps): ReactElement => {
  const { navigateToHome } = useImbueNavigate();
  const updateProjects = useSetAtom(updateProjectsAtom);
  const {
    shouldShowInitialCommitDialog,
    setShouldShowInitialCommitDialog,
    pendingPath,
    setToast,
    setShouldShowAddProjectDialog,
  } = props;

  const handleInitialCommit = async (): Promise<void> => {
    setShouldShowInitialCommitDialog(false);
    setShouldShowAddProjectDialog(false);

    try {
      await createInitialCommit({
        body: { projectPath: pendingPath },
        meta: { skipWsAck: true },
      });

      const { data: project } = await initializeProject({
        body: { projectPath: pendingPath },
      });

      // Get the updated project list
      const { data: projects } = await listProjects();
      updateProjects(projects);

      navigateToHome(project.objectId);
    } catch (initError) {
      let errorMessage = "Failed to create initial commit";
      if (initError instanceof HTTPException) {
        errorMessage = initError.detail;
      } else if (initError instanceof Error) {
        errorMessage = initError.message;
      }
      setToast({ title: errorMessage, type: ToastType.ERROR });
    }
  };

  const handleCancelInitialCommit = (): void => {
    setShouldShowInitialCommitDialog(false);
    setShouldShowAddProjectDialog(false);
    setToast({
      title: "Please make an initial commit to the repository",
      description: <Link href="https://git-scm.com/docs/gittutorial" />,
      type: ToastType.ERROR,
    });
  };
  return (
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
              data-testid={ElementIds.PROJECT_INITIAL_COMMIT_CONFIRM}
            >
              Make Initial Commit
            </Button>
          </AlertDialog.Action>
        </Flex>
      </AlertDialog.Content>
    </AlertDialog.Root>
  );
};
