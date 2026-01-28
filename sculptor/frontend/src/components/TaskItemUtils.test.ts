import type { CodingAgentTaskView } from "../api";
import { getRelativeTime, getStyledBranchName } from "./TaskItemUtils.ts";

describe("TaskItemUtils", () => {
  describe("getStyledBranchName", () => {
    const mockTask: CodingAgentTaskView = {} as CodingAgentTaskView;

    it("should return null when getBranchName returns null", () => {
      const result = getStyledBranchName(mockTask);
      expect(result).toBeNull();
    });

    it("should return formatted branch name when getBranchName returns a valid branch name", () => {
      mockTask.branchName = "feature/new-component";
      const result = getStyledBranchName(mockTask);

      expect(result).toBe("- feature/new-component");
    });
  });

  describe("getRelativeTime", () => {
    const SECOND = 1000;
    const MINUTE = 60 * SECOND;
    const HOUR = 60 * MINUTE;
    const DAY = 24 * HOUR;
    const WEEK = 7 * DAY;
    const MONTH = 30 * DAY;

    describe("with current timestamp as now parameter", () => {
      const baseTime = 1609459200000; // Jan 1, 2021 00:00:00 GMT

      it('should return "Just now" for timestamps within the last 60 seconds', () => {
        const now = baseTime;
        expect(getRelativeTime(baseTime - 30 * SECOND, now)).toBe("Just now");
        expect(getRelativeTime(baseTime - 59 * SECOND, now)).toBe("Just now");
        expect(getRelativeTime(baseTime, now)).toBe("Just now");
      });
    });

    describe("with string timestamps", () => {
      const baseTime = 1609459200000; // Jan 1, 2021 00:00:00 GMT

      it("should handle ISO string timestamps correctly", () => {
        const now = baseTime;
        const timestamp = new Date(baseTime - 2 * MINUTE).toISOString();

        expect(getRelativeTime(timestamp, now)).toBe("2 mins ago");
      });

      it("should handle date string timestamps correctly", () => {
        const now = baseTime;
        const timestamp = new Date(baseTime - 5 * HOUR).toString();

        expect(getRelativeTime(timestamp, now)).toBe("5 hrs ago");
      });
    });

    describe("with Python-style timestamps (seconds)", () => {
      const baseTime = 1609459200000; // Jan 1, 2021 00:00:00 GMT
      const baseTimeSeconds = Math.floor(baseTime / 1000);

      it("should convert Python timestamps (seconds) to milliseconds correctly", () => {
        const now = baseTime;
        const pythonTimestamp = baseTimeSeconds - 30 * 60; // 30 minutes ago in seconds

        expect(getRelativeTime(pythonTimestamp, now)).toBe("30 mins ago");
      });
    });

    describe("singular vs plural forms", () => {
      const baseTime = 1609459200000;

      it("should use singular form for 1 unit", () => {
        const now = baseTime;
        expect(getRelativeTime(baseTime - 1 * MINUTE, now)).toBe("1 min ago");
        expect(getRelativeTime(baseTime - 1 * HOUR, now)).toBe("1 hr ago");
        expect(getRelativeTime(baseTime - 1 * DAY, now)).toBe("1 day ago");
        expect(getRelativeTime(baseTime - 1 * WEEK, now)).toBe("1 wk ago");
        expect(getRelativeTime(baseTime - 1 * MONTH, now)).toBe("1 mon ago");
      });

      it("should use plural form for multiple units", () => {
        const now = baseTime;
        expect(getRelativeTime(baseTime - 2 * MINUTE, now)).toBe("2 mins ago");
        expect(getRelativeTime(baseTime - 2 * HOUR, now)).toBe("2 hrs ago");
        expect(getRelativeTime(baseTime - 2 * DAY, now)).toBe("2 days ago");
        expect(getRelativeTime(baseTime - 2 * WEEK, now)).toBe("2 wks ago");
        expect(getRelativeTime(baseTime - 2 * MONTH, now)).toBe("2 mons ago");
      });
    });
  });
});
