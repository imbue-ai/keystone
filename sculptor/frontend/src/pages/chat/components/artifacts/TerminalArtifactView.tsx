import { Flex, Text } from "@radix-ui/themes";
import { Terminal } from "lucide-react";
import type { ReactElement } from "react";
import { useMemo, useRef } from "react";

import { TaskStatus } from "../../../../api";
import { mergeClasses, useResolvedTheme } from "../../../../common/Utils.ts";
import type { ArtifactViewContentProps, ArtifactViewTabLabelProps } from "../../Types.ts";
import styles from "./TerminalArtifactView.module.scss";

// TODO (5c07b523-636b-4bf7-a925-47eaba9ae833) make the typing for input artifacts more strict with generics
export const TerminalArtifactViewComponent = ({ task }: ArtifactViewContentProps): ReactElement => {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const appTheme = useResolvedTheme();

  // Override terminalUrl to null when task is in ERROR state
  // This ensures we show our error recovery UI instead of ttyd's reconnect overlay
  const terminalUrl = task.status === TaskStatus.ERROR ? null : task?.serverUrlByName?.terminal;

  const theme = useMemo(() => {
    if (appTheme === "dark") {
      return {
        background: "#1A1814",
        foreground: "#E6E2DC",
      };
    }
    return {
      background: "#FDFDFC",
      foreground: "#3B352B",
    };
  }, [appTheme]);

  const terminalIframe = useMemo(() => {
    if (!terminalUrl) return null;
    const parsedTerminalUrl = new URL(terminalUrl);
    parsedTerminalUrl.searchParams.set("theme", JSON.stringify(theme));
    return (
      <iframe
        ref={iframeRef}
        src={`${parsedTerminalUrl.href}`}
        frameBorder="0"
        title="Remote Content"
        className={styles.terminalIframe}
        sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
      />
    );
  }, [terminalUrl, theme]);

  // Show different messages based on task status when terminal is not available
  if (!terminalUrl) {
    // If task is in error state, show error message
    if (task.status === TaskStatus.ERROR) {
      return <Text color="gray">The agent is in an error state. Terminal is not available.</Text>;
    }

    // If task is building or running (terminal still initializing), show building message
    if (task.status === TaskStatus.BUILDING || task.status === TaskStatus.RUNNING) {
      return <Text color="gray">Container is building, just a few moments...</Text>;
    }

    // Default message for other cases
    return <Text color="gray">Terminal not available</Text>;
  }

  return (
    <div className={styles.terminalContainer}>
      <div className={mergeClasses(styles.iframeWrapper, appTheme === "light" ? styles.lightMode : styles.darkMode)}>
        {terminalIframe}
      </div>
      <div className={mergeClasses(styles.infoBarWrapper, appTheme === "light" ? styles.lightMode : styles.darkMode)}>
        <div className={styles.infoBar}>
          <Text size="2" color="gray" weight="medium">
            tmux session inside of Agent&apos;s container
          </Text>
        </div>
      </div>
    </div>
  );
};

export const TerminalTabLabelComponent = ({ shouldShowIcon = true }: ArtifactViewTabLabelProps): ReactElement => {
  return (
    <Flex align="center" gap="2">
      {shouldShowIcon && <Terminal />}
      Terminal
    </Flex>
  );
};
