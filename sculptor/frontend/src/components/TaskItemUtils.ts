import type { CodingAgentTaskView } from "../api";

//NOTE: To run the tests, call `cd frontend && npm test TaskItemUtils.test.ts` (only works in nvm version v20.19.4 or higher)

export const getStyledBranchName = (task: Pick<CodingAgentTaskView, "branchName">): string | null => {
  if (!task.branchName) return null;
  return `- ${task.branchName}`;
};

// Time unit constants
const TIME_UNITS = {
  SECOND: 1000,
  MINUTE: 60 * 1000,
  HOUR: 60 * 60 * 1000,
  DAY: 24 * 60 * 60 * 1000,
  WEEK: 7 * 24 * 60 * 60 * 1000,
  MONTH: 30 * 24 * 60 * 60 * 1000,
} as const;

// Time unit definitions for relative time formatting
// This structure makes it easy to extract for i18n
const RELATIVE_TIME_UNITS = [
  {
    threshold: TIME_UNITS.HOUR,
    unit: TIME_UNITS.MINUTE,
    singular: "min",
    plural: "mins",
  },
  {
    threshold: TIME_UNITS.DAY,
    unit: TIME_UNITS.HOUR,
    singular: "hr",
    plural: "hrs",
  },
  {
    threshold: TIME_UNITS.WEEK,
    unit: TIME_UNITS.DAY,
    singular: "day",
    plural: "days",
  },
  {
    threshold: TIME_UNITS.MONTH,
    unit: TIME_UNITS.WEEK,
    singular: "wk",
    plural: "wks",
  },
] as const;

// i18n-ready strings - these can be easily extracted to translation files
const RELATIVE_TIME_STRINGS = {
  justNow: "Just now",
  ago: "ago",
} as const;

/**
 * Converts a timestamp to milliseconds, handling both JavaScript (ms) and Python (s) timestamps
 */
export const normalizeTimestamp = (timestamp: number | string): number => {
  if (typeof timestamp === "string") {
    return new Date(timestamp).getTime();
  }

  // Python datetime typically returns seconds, JS uses milliseconds
  return timestamp < 10000000000 ? timestamp * 1000 : timestamp;
};

/**
 * Formats a time unit with proper pluralization
 */
const formatTimeUnit = (value: number, singular: string, plural: string): string => {
  const unit = value === 1 ? singular : plural;
  return `${value} ${unit} ${RELATIVE_TIME_STRINGS.ago}`;
};

export const getRelativeTime = (timestamp: number | string, now: number = Date.now()): string => {
  const timestampMs = normalizeTimestamp(timestamp);
  const diffInMs = now - timestampMs;

  // Handle future timestamps or very recent timestamps
  if (diffInMs < TIME_UNITS.MINUTE) {
    return RELATIVE_TIME_STRINGS.justNow;
  }

  // Find the appropriate time unit
  for (const { threshold, unit, singular, plural } of RELATIVE_TIME_UNITS) {
    if (diffInMs < threshold) {
      const value = Math.floor(diffInMs / unit);
      return formatTimeUnit(value, singular, plural);
    }
  }

  // For anything older than a month, use months
  const months = Math.floor(diffInMs / TIME_UNITS.MONTH);
  return formatTimeUnit(months, "mon", "mons");
};
