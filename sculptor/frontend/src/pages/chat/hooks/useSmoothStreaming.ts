import { useAtomValue } from "jotai";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { ChatMessage } from "~/api";
import { useTaskPageParams } from "~/common/NavigateUtils.ts";
import { isSmoothStreamingEnabledAtom } from "~/common/state/atoms/smoothStreaming.ts";
import { useTask } from "~/common/state/hooks/useTaskHelpers.ts";

import { registerEngine, StreamingEngine, unregisterEngine } from "../utils/StreamingEngine.ts";

const SMOOTH_STREAM_INTERVAL_MS = 50;

/**
 * Orchestrates a StreamingEngine against live task snapshots.
 * - Feeds each in-progress ChatMessage snapshot into the engine (`updateLatestSnapshot`).
 * - Drives the engine's tick loop on a fixed interval while there are pending chunks.
 * - Flushes or clears state as soon as smooth streaming is disabled or the backend finishes the message.
 *
 * Callers just supply the latest snapshot; the hook returns the rendered ChatMessage the UI should display.
 */
export const useChatSmoothStreaming = (chatMessage: ChatMessage | null): ChatMessage | null => {
  const { taskID } = useTaskPageParams();
  const task = useTask(taskID);
  const isSmoothStreamingEnabledForTask = task?.isSmoothStreamingSupported ?? false;
  const isSmoothStreamingEnabled = useAtomValue(isSmoothStreamingEnabledAtom) && isSmoothStreamingEnabledForTask;

  const engineRef = useRef<StreamingEngine | null>(null);
  const drainTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const activeMessageIdRef = useRef<string | null>(null);
  const activeTaskIdRef = useRef<string | null>(null);
  const shouldFinalizeOnIdleRef = useRef<boolean>(false);
  const [renderedMessage, setRenderedMessage] = useState<ChatMessage | null>(chatMessage ?? null);

  const ensureEngine = useCallback((): StreamingEngine => {
    if (!engineRef.current) {
      engineRef.current = new StreamingEngine();
      registerEngine(engineRef.current);
    }
    return engineRef.current;
  }, []);

  const stopDrainLoop = useCallback((): void => {
    if (drainTimerRef.current !== null) {
      clearInterval(drainTimerRef.current);
      drainTimerRef.current = null;
    }
  }, []);

  const clearStreamingState = useCallback((): void => {
    stopDrainLoop();
    shouldFinalizeOnIdleRef.current = false;
    activeMessageIdRef.current = null;
    activeTaskIdRef.current = null;
    const engine = ensureEngine();
    engine.updateLatestSnapshot(null);
    setRenderedMessage(null);
  }, [ensureEngine, stopDrainLoop]);

  // Initialize the streaming engine on mount
  useEffect(() => {
    ensureEngine();

    return (): void => {
      stopDrainLoop();
      if (engineRef.current) {
        unregisterEngine(engineRef.current);
        engineRef.current.updateLatestSnapshot(null);
      }
    };
  }, [ensureEngine, stopDrainLoop]);

  const ensureDrainLoop = useCallback((): void => {
    if (drainTimerRef.current !== null) {
      return;
    }

    // One interval drives all intermediate renders until the queue empties again.
    drainTimerRef.current = setInterval(() => {
      const engine = ensureEngine();

      if (!engine.hasPendingChunks()) {
        stopDrainLoop();
        if (shouldFinalizeOnIdleRef.current) {
          shouldFinalizeOnIdleRef.current = false;
          clearStreamingState();
        }
        return;
      }

      const next = engine.tick();
      setRenderedMessage(next);
    }, SMOOTH_STREAM_INTERVAL_MS);
  }, [clearStreamingState, ensureEngine, stopDrainLoop]);

  // Called when backend sent `null` for the in-progress message; resolve any remaining animation work.
  const handleStreamCompletion = useCallback(
    (engine: StreamingEngine, smoothStreamingEnabled: boolean): void => {
      if (!activeMessageIdRef.current) {
        clearStreamingState();
        return;
      }

      // If the task changed, this is a task switch, not a message completion.
      // Clear immediately without draining remaining chunks.
      const isTaskSwitch = activeTaskIdRef.current !== null && activeTaskIdRef.current !== taskID;
      if (isTaskSwitch) {
        stopDrainLoop();
        clearStreamingState();
        return;
      }

      if (!smoothStreamingEnabled) {
        stopDrainLoop();
        engine.flush();
        clearStreamingState();
        return;
      }

      if (engine.hasPendingChunks()) {
        shouldFinalizeOnIdleRef.current = true;
        ensureDrainLoop();
        return;
      }

      clearStreamingState();
    },
    [clearStreamingState, ensureDrainLoop, stopDrainLoop, taskID],
  );

  // immediately sync the engine to the snapshot with no queue animation.
  const renderSnapshotWithoutAnimation = useCallback(
    (engine: StreamingEngine, snapshot: ChatMessage): void => {
      stopDrainLoop();
      shouldFinalizeOnIdleRef.current = false;
      engine.updateLatestSnapshot(snapshot);
      activeMessageIdRef.current = snapshot.id;
      activeTaskIdRef.current = taskID;
      setRenderedMessage(engine.flush());
    },
    [stopDrainLoop, taskID],
  );

  const handleStreamingSnapshot = useCallback(
    (engine: StreamingEngine, snapshot: ChatMessage): void => {
      shouldFinalizeOnIdleRef.current = false;
      activeTaskIdRef.current = taskID;

      if (!activeMessageIdRef.current) {
        activeMessageIdRef.current = snapshot.id;
        engine.updateLatestSnapshot(snapshot);
        setRenderedMessage(engine.flush());
        return;
      }

      if (activeMessageIdRef.current !== snapshot.id) {
        activeMessageIdRef.current = snapshot.id;
        engine.updateLatestSnapshot(snapshot);
        setRenderedMessage(engine.flush());
        return;
      }

      engine.updateLatestSnapshot(snapshot);
      const nextRendered = engine.render();
      setRenderedMessage(nextRendered);
      ensureDrainLoop();
    },
    [ensureDrainLoop, taskID],
  );

  // Every time this hook is called with a new snapshot, reconcile it against the engine state and schedule ticks.
  useEffect(() => {
    const engine = ensureEngine();

    if (!chatMessage) {
      handleStreamCompletion(engine, isSmoothStreamingEnabled);
      return;
    }

    if (!isSmoothStreamingEnabled) {
      renderSnapshotWithoutAnimation(engine, chatMessage);
      return;
    }

    handleStreamingSnapshot(engine, chatMessage);
  }, [
    chatMessage,
    handleStreamCompletion,
    handleStreamingSnapshot,
    isSmoothStreamingEnabled,
    renderSnapshotWithoutAnimation,
    ensureEngine,
  ]);

  return useMemo(() => {
    // Synchronous guard: if we switched to a different task, don't return stale
    // animated text from the previous task. The effect will clean up properly,
    // but this prevents the 1-frame glitch before the effect runs.
    if (renderedMessage && activeTaskIdRef.current !== null && activeTaskIdRef.current !== taskID) {
      return null;
    }
    return renderedMessage ?? null;
  }, [renderedMessage, taskID]);
};
