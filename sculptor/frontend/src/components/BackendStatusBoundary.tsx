import { Box, Flex, Progress, Text } from "@radix-ui/themes";
import { useAtom, useSetAtom } from "jotai";
import type { PropsWithChildren, ReactElement } from "react";
import { useCallback, useEffect, useRef } from "react";

import { getHealthCheck } from "~/api";

import SculptorLogoAndTitle from "../assets/logos/sculptor_logo_and_title.svg";
import {
  backendStatusAtom,
  hasBackendStartedSuccessfullyAtom,
  healthCheckDataAtom,
} from "../common/state/atoms/backend.ts";
import { ErrorPage } from "../pages/error/ErrorPage.tsx";
import type { AnyBackendStatus, BackendStatus } from "../shared/types.ts";
import styles from "./BackendStatusBoundary.module.scss";
import { TitleBar } from "./TitleBar.tsx";

const isBackendStatusExited = (state: AnyBackendStatus): state is BackendStatus<"exited"> => {
  return state.status === "exited";
};

const isBackendStatusError = (state: AnyBackendStatus): state is BackendStatus<"error"> => {
  return state.status === "error";
};

type BackendStatusBoundaryProps = {
  setIsBackendAPIReady?: (isReady: boolean) => void;
};

export const BackendStatusBoundary = (props: PropsWithChildren<BackendStatusBoundaryProps>): ReactElement => {
  const [backendStatus, setBackendStatus] = useAtom(backendStatusAtom);
  const [hasStartedSuccessfully, setHasStartedSuccessfully] = useAtom(hasBackendStartedSuccessfullyAtom);
  const healthCheckInterval = useRef<NodeJS.Timeout | null>(null);
  const setHealthCheckData = useSetAtom(healthCheckDataAtom);

  const { setIsBackendAPIReady } = props;
  const maybeSetBackendStatus = useCallback(
    (newStatus: AnyBackendStatus): void => {
      let isSet = false;
      // We use the functional form of setState to avoid circular dependencies.
      setBackendStatus((prevStatus) => {
        if (prevStatus.status === "shutting_down") {
          // Once shutting down, always stay shutting down. Ignore any other updates.
          return prevStatus;
        }
        isSet = true;
        return newStatus;
      });

      if (isSet) {
        if (newStatus.status === "running") {
          setHasStartedSuccessfully(true);
        }

        const isBackendAPIReady = newStatus.status === "running" || newStatus.status === "warning";
        setIsBackendAPIReady?.(isBackendAPIReady);
      }
    },
    [setBackendStatus, setHasStartedSuccessfully, setIsBackendAPIReady],
  );

  useEffect(() => {
    if (!window.sculptor) return;

    const loadInitialState = async (): Promise<void> => {
      if (!window.sculptor) return;

      try {
        const initialState = await window.sculptor.getCurrentBackendStatus();
        maybeSetBackendStatus(initialState);
      } catch (error) {
        console.error("Failed to load initial backend state:", error);
      }
    };

    loadInitialState();

    const handleStateChange = (state: AnyBackendStatus): void => {
      console.log(`backend state change: ${state}`);
      maybeSetBackendStatus(state);
    };

    window.sculptor.onBackendStatusChange(handleStateChange);

    return (): void => {
      window.sculptor?.removeBackendStatusListener?.();
    };
  }, [setHasStartedSuccessfully, maybeSetBackendStatus]);

  useEffect(() => {
    if (backendStatus.status === "shutting_down") {
      // Don't perform health checks while shutting down.
      return;
    }

    const performHealthCheck = async (): Promise<void> => {
      try {
        const { data: healthData } = await getHealthCheck({
          meta: { skipWsAck: true },
        });

        // mark a successful start if we haven't yet
        if (!hasStartedSuccessfully) {
          setHasStartedSuccessfully(true);
        }

        setHealthCheckData(healthData);

        if (healthData && healthData.freeDiskGb < healthData.minFreeDiskGb) {
          maybeSetBackendStatus({
            status: "warning",
            payload: {
              message:
                "Insufficient free space (" +
                Number(healthData.freeDiskGb).toFixed(2) +
                " GB free, " +
                healthData.minFreeDiskGb +
                " GB required)) You must free up additional space before creating new tasks or messages",
            },
          });
        } else if (healthData && healthData.freeDiskGb < healthData.freeDiskGbWarnLimit) {
          maybeSetBackendStatus({
            status: "warning",
            payload: {
              message:
                "Low disk space warning (only " +
                Number((healthData.freeDiskGbWarnLimit - healthData.freeDiskGb).toFixed(2)) +
                " GB free) Please free up some space (no new tasks or messages will be allowed when free space <= " +
                healthData.minFreeDiskGb +
                " GB).",
            },
          });
        } else {
          maybeSetBackendStatus({
            status: "running",
            payload: { message: "Received health check response from backend." },
          });
        }
      } catch (error) {
        console.log("Backend health check failed:", error);

        // if we've never started, exit and stay in the loading state
        if (!hasStartedSuccessfully) return;

        maybeSetBackendStatus({
          status: "unresponsive",
          payload: {
            message: "The backend process is down or unresponsive. Please restart the application.",
          },
        });
      }
    };

    healthCheckInterval.current = setInterval(performHealthCheck, 3000);

    return (): void => {
      if (healthCheckInterval.current) {
        clearInterval(healthCheckInterval.current);
        healthCheckInterval.current = null;
      }
    };
  }, [
    backendStatus.status,
    maybeSetBackendStatus,
    hasStartedSuccessfully,
    setHasStartedSuccessfully,
    setHealthCheckData,
  ]);

  if (backendStatus.status === "loading") {
    return (
      <Flex height="100vh" width="100wh" className={styles.background}>
        <TitleBar />
        <Flex m="auto" gap="4" align="center" direction="column">
          <Flex align="center" gap="1">
            <img src={SculptorLogoAndTitle} alt="Sculptor Logo and Title" />
            <Box className={styles.betaLabel}>beta</Box>
          </Flex>
          <Box width="178px">
            <Progress duration="10s" />
          </Box>
        </Flex>
      </Flex>
    );
  }

  if (backendStatus.status === "shutting_down") {
    return (
      <Flex height="100vh" width="100wh" className={styles.background}>
        <TitleBar />
        <Flex m="auto" gap="4" align="center" direction="column">
          <Flex align="center" gap="1">
            <img src={SculptorLogoAndTitle} alt="Sculptor Logo and Title" />
            <Box className={styles.betaLabel}>beta</Box>
          </Flex>
          <Box width="178px">
            {/*
                We optimistically expect shutdown to be generally fast.
                When that's not the case, the Progress component automatically switches to an indeterminate state after its duration elapses.
                We should gradually remove all cases where shutdown takes a long time, anyway.
            */}
            <Progress duration="3s" />
          </Box>
          <Text size="3" weight="medium" className={styles.shutdownLabel}>
            Shutting down...
          </Text>
        </Flex>
      </Flex>
    );
  }

  // Fatal error state - show error page if we never got running
  if ((isBackendStatusExited(backendStatus) && !hasStartedSuccessfully) || isBackendStatusError(backendStatus)) {
    const errorMessage =
      backendStatus.status === "exited" ? backendStatus.payload.stderr : backendStatus.payload.message;

    return (
      <>
        <ErrorPage
          isCapturingErrorWithSentry={false}
          headerText="Oops! That is embarrassing. An unexpected error has occurred. Try restarting the app or contacting us if the problem persists."
          errorMessage={errorMessage}
        />
      </>
    );
  }

  return <>{props.children}</>;
};
