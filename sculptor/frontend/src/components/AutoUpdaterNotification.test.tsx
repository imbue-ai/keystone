import { render, screen } from "@testing-library/react";
import type { ProgressInfo, UpdateInfo } from "electron-updater";
import type React from "react";
import { act } from "react";

import type { AutoUpdaterState } from "../hooks/useAutoUpdater";
import { useAutoUpdater } from "../hooks/useAutoUpdater";
import { AutoUpdaterNotification } from "./AutoUpdaterNotification";

// Mock the useAutoUpdater hook
jest.mock("../hooks/useAutoUpdater");

const mockUseAutoUpdater = useAutoUpdater as jest.MockedFunction<typeof useAutoUpdater>;

// Mock the SCSS module
jest.mock("./AutoUpdaterNotification.module.scss", () => ({
  auViewport: "auViewport",
  auCard: "auCard",
  fadeIn: "fadeIn",
  fadeOut: "fadeOut",
  title: "title",
  updateVersion: "updateVersion",
}));

// Helper to create mock UpdateInfo
const createMockUpdateInfo = (version: string): UpdateInfo => ({
  version,
  files: [],
  path: "",
  sha512: "",
  releaseDate: new Date().toISOString(),
});

// Helper to create mock ProgressInfo
const createMockProgressInfo = (percent: number): ProgressInfo => ({
  total: 100,
  delta: 10,
  transferred: percent,
  percent,
  bytesPerSecond: 1000,
});

// Default mock action functions
const mockActions = {
  checkForUpdates: jest.fn(),
  downloadUpdate: jest.fn(),
  quitAndInstall: jest.fn(),
  setAutoDownload: jest.fn(),
};

// Helper to set up mock hook state
const setupMockState = (state: Partial<AutoUpdaterState>): void => {
  mockUseAutoUpdater.mockReturnValue({
    status: "idle",
    ...state,
    ...mockActions,
  });
};

// Helper to rerender with new state
const rerenderWithState = (rerender: (ui: React.ReactElement) => void, state: Partial<AutoUpdaterState>): void => {
  setupMockState(state);

  act(() => {
    rerender(<AutoUpdaterNotification />);
  });
};

// Helper to advance timers within act
const advanceTimers = (ms: number): void => {
  act(() => {
    jest.advanceTimersByTime(ms);
  });
};

// Helper to get card element by status
const getCardByStatus = (status: string): Element | null => {
  return screen.getByRole("status").querySelector(`[data-status="${status}"]`);
};

describe("AutoUpdaterNotification", () => {
  beforeEach(() => {
    jest.clearAllMocks();

    jest.useFakeTimers();

    setupMockState({ status: "idle" });
  });

  afterEach(() => {
    act(() => {
      jest.runOnlyPendingTimers();
    });

    jest.useRealTimers();
  });

  describe("interaction delay behavior", () => {
    it("should initially render with pointerEvents=none during animation", () => {
      setupMockState({
        status: "downloaded",
        updateInfo: createMockUpdateInfo("1.0.0"),
      });

      render(<AutoUpdaterNotification />);

      const card = getCardByStatus("downloaded");

      expect(card).toHaveStyle({ pointerEvents: "none" });
    });

    it("should restore interactivity after 300ms initial animation", () => {
      setupMockState({
        status: "downloaded",
        updateInfo: createMockUpdateInfo("1.0.0"),
      });

      render(<AutoUpdaterNotification />);

      const card = getCardByStatus("downloaded");

      expect(card).toHaveStyle({ pointerEvents: "none" });

      advanceTimers(300);

      expect(card).toHaveStyle({ pointerEvents: "auto" });
    });
  });

  describe("race condition: close during animation then reopen", () => {
    it("should properly clean up timeouts when component unmounts during animation", () => {
      const { rerender, unmount } = render(<AutoUpdaterNotification />);

      rerenderWithState(rerender, {
        status: "downloaded",
        updateInfo: createMockUpdateInfo("1.0.0"),
      });

      advanceTimers(150);

      unmount();

      advanceTimers(200);

      expect(jest.getTimerCount()).toBe(0);
    });
  });

  describe("race condition: status change during cross-fade", () => {
    it("should handle status changes during cross-fade animation without racing timeouts", () => {
      const { rerender } = render(<AutoUpdaterNotification />);

      rerenderWithState(rerender, {
        status: "available",
        updateInfo: createMockUpdateInfo("1.0.0"),
      });

      advanceTimers(300);

      rerenderWithState(rerender, {
        status: "downloading",
        updateInfo: createMockUpdateInfo("1.0.0"),
        downloadProgress: createMockProgressInfo(50),
      });

      advanceTimers(500);

      advanceTimers(250);

      rerenderWithState(rerender, {
        status: "downloaded",
        updateInfo: createMockUpdateInfo("1.0.0"),
      });

      const card = getCardByStatus("downloaded");

      expect(card).toHaveStyle({ pointerEvents: "none" });

      advanceTimers(1000);

      expect(card).toHaveStyle({ pointerEvents: "auto" });

      expect(jest.getTimerCount()).toBe(0);
    });
  });

  describe("basic rendering", () => {
    it("should not render when status is idle", () => {
      setupMockState({ status: "idle" });

      render(<AutoUpdaterNotification />);

      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    });

    it("should render when update is downloaded", () => {
      setupMockState({
        status: "downloaded",
        updateInfo: createMockUpdateInfo("1.0.0"),
      });

      render(<AutoUpdaterNotification />);

      expect(screen.getByText("Update installed and ready")).toBeInTheDocument();
    });
  });
});
