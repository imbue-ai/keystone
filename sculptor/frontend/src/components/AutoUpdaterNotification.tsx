import { Box, Button, Card, Flex, IconButton, Link, Progress, Text } from "@radix-ui/themes";
import { ExternalLink, X } from "lucide-react";
import type { ReactElement } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import { useAutoUpdater } from "../hooks/useAutoUpdater";
import styles from "./AutoUpdaterNotification.module.scss";

const SCULPTOR_CHANGELOG_LINK = "https://imbue-1.gitbook.io/imbue-docs/changelog";

type NotificationContent = {
  title: string;
  version?: string;
  description?: string;
  percent?: number;
  restartButton?: boolean;
};

type Status = "idle" | "checking" | "available" | "not-available" | "downloading" | "downloaded" | "error";

export const AutoUpdaterNotification = (): ReactElement | null => {
  const { status, updateInfo, downloadProgress, error, currentVersion, quitAndInstall } = useAutoUpdater();
  const [isVisible, setIsVisible] = useState(false);
  const prevStatusRef = useRef<Status | null>(null);
  const dismissedKeyRef = useRef<string | null>(null);
  const key = `${status}:${updateInfo?.version ?? ""}`;

  // Used for animating cross-fades of notifications
  const [isAnimatingOut, setIsAnimatingOut] = useState(false);
  const [isAnimatingIn, setIsAnimatingIn] = useState(false);
  const [displayedKey, setDisplayedKey] = useState<string>(key);
  const FADE_MS = 500;

  const content: NotificationContent | null = useMemo(() => {
    switch (status as Status) {
      case "idle":
        return null;
      case "checking":
        return null;
      case "available":
        return {
          title: "Update available",
          version: updateInfo?.version,
          description: "Your update will begin shortly.",
        };
      case "not-available":
        return {
          title: "You're up to date",
          version: currentVersion ? `Version ${currentVersion}` : undefined,
        };
      case "downloading": {
        const percent = downloadProgress ? Math.round(downloadProgress.percent) : undefined;
        return {
          title: "Update downloading...",
          version: updateInfo?.version,
          description: percent != null ? `${percent}% complete` : "Starting download...",
          percent,
        };
      }
      case "downloaded":
        return {
          title: "Update installed and ready",
          version: updateInfo?.version,
          restartButton: true,
        };
      case "error":
        return {
          title: "Update error",
          description: error?.message || "An error occurred while updating",
        };
      default:
        return null;
    }
  }, [status, updateInfo?.version, downloadProgress, error?.message, currentVersion]);

  // show on meaningful transitions unless the same key was dismissed
  useEffect(() => {
    const prev = prevStatusRef.current;
    if (prev === status && dismissedKeyRef.current === key) return;

    const hasChanged = prev !== status;
    const didDismiss = dismissedKeyRef.current === key;

    if ((hasChanged || !didDismiss) && content) {
      setIsVisible(true);
      dismissedKeyRef.current = null;
      prevStatusRef.current = status;
    }
  }, [status, key, content]);

  const [displayedContent, setDisplayedContent] = useState<NotificationContent | null>(content);

  // auto-hide only for ephemeral states
  useEffect(() => {
    if (!isVisible) return;
    const isEphemeral = status === "not-available" || status === "error";
    if (!isEphemeral) return;

    const t = window.setTimeout(() => setIsVisible(false), 5000);
    return (): void => window.clearTimeout(t);
  }, [isVisible, status]);

  const handleClose = (): void => {
    dismissedKeyRef.current = key;
    setIsVisible(false);
  };

  // Handle initial animation when notification becomes visible
  useEffect(() => {
    if (!isVisible) return;

    // Set animating state for initial appearance
    setIsAnimatingIn(true);

    // Clear after animation completes (300ms from SCSS default animation)
    const timeout = window.setTimeout(() => {
      setIsAnimatingIn(false);
    }, 300);

    return (): void => window.clearTimeout(timeout);
  }, [isVisible]);

  // When the *source* content changes while visible, fade-out then swap.
  useEffect(() => {
    if (!isVisible || !content) return;

    if (displayedKey === key) {
      // Same "card": keep content updated (progress percent, etc.)
      setDisplayedContent(content);
      return;
    }

    // New "card": fade out old, then replace & fade in new
    setIsAnimatingOut(true);
    let fadeInTimeout: number | undefined;

    const fadeOutTimeout = window.setTimeout(() => {
      setDisplayedKey(key);
      setDisplayedContent(content);
      setIsAnimatingOut(false);
      setIsAnimatingIn(true);

      // Clear isAnimatingIn after animation completes
      fadeInTimeout = window.setTimeout(() => {
        setIsAnimatingIn(false);
      }, FADE_MS);
    }, FADE_MS);

    return (): void => {
      window.clearTimeout(fadeOutTimeout);
      if (fadeInTimeout !== undefined) {
        window.clearTimeout(fadeInTimeout);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, content, isVisible]);

  if (!displayedContent || !isVisible) return null;

  return (
    <Box className={styles.auViewport} role="status" aria-live="polite" minWidth="280px" maxWidth="360px">
      <Card
        className={`${styles.auCard} ${isAnimatingOut ? styles.fadeOut : styles.fadeIn}`}
        size="2"
        variant="surface"
        data-status={status}
        style={{ minWidth: 280, maxWidth: 360, pointerEvents: isAnimatingIn || isAnimatingOut ? "none" : "auto" }}
      >
        <Flex direction="column" gap="2" mb="1">
          <Flex align="center" justify="between" gap="2">
            <Flex gap="2" align="center" minWidth="0">
              <Text className={styles.title} weight="medium" truncate>
                {displayedContent.title}
              </Text>
              <Text size="2" className={styles.updateVersion}>
                {displayedContent.version}
              </Text>
            </Flex>
            <Flex>
              <IconButton aria-label="Close notification" variant="ghost" size="1" onClick={handleClose}>
                <X />
              </IconButton>
            </Flex>
          </Flex>
          <Flex align="center" justify="between" gap="2">
            <Text className={styles.title} weight="medium" truncate>
              <Link href={SCULPTOR_CHANGELOG_LINK} target="_blank" rel="noopener noreferrer">
                <Flex align="center" gap="1">
                  See what&apos;s new
                  <ExternalLink size={14} />
                </Flex>
              </Link>
            </Text>
          </Flex>
          <Flex direction="column" gap="1" flexGrow="1" minWidth="0">
            {displayedContent.description && <Text size="2">{displayedContent.description}</Text>}
            {displayedContent.restartButton && (
              <Button variant="solid" onClick={quitAndInstall}>
                Restart & Update
              </Button>
            )}

            {status === "downloading" && displayedContent.percent != null && (
              <Progress mt="2" size="1" value={displayedContent.percent} aria-label="Download progress" />
            )}
          </Flex>
        </Flex>
      </Card>
    </Box>
  );
};
