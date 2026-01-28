import { LlmModel } from "~/api";

export const CHAT_INPUT_ELEMENT_ID = "chat-input" as const;
export const THINKING_INDICATOR_ELEMENT_ID = "thinking-indicator" as const;

export const TOTAL_CONTEXT_WINDOW_TOKENS_BY_MODEL: Record<LlmModel, number> = {
  [LlmModel.CLAUDE_4_SONNET]: 200_000,
  [LlmModel.CLAUDE_4_HAIKU]: 200_000,
  [LlmModel.CLAUDE_4_OPUS]: 200_000,
  [LlmModel.GPT_5_1]: 272_000,
  [LlmModel.GPT_5_1_CODEX]: 272_000,
  [LlmModel.GPT_5_1_CODEX_MINI]: 272_000,
  [LlmModel.GPT_5_2]: 272_000,
};

export const MAX_CONTEXT_WINDOW_TOKENS_BEFORE_FORCED_COMPACT_BY_MODEL: Record<LlmModel, number> = {
  [LlmModel.CLAUDE_4_SONNET]: 180_000,
  [LlmModel.CLAUDE_4_HAIKU]: 180_000,
  [LlmModel.CLAUDE_4_OPUS]: 180_000,
  [LlmModel.GPT_5_1]: 272_000,
  [LlmModel.GPT_5_1_CODEX]: 272_000,
  [LlmModel.GPT_5_1_CODEX_MINI]: 272_000,
  [LlmModel.GPT_5_2]: 272_000,
};

export const MAX_CONTEXT_WINDOW_TOKENS_BEFORE_FORCED_COMPACT = 170000 as const;

// Breakpoints for responsive layout

export const NARROW_PROJECT_LAYOUT_BREAKPOINT = 900;
export const NARROW_HEADER_BREAKPOINT = 600;
export const ARTIFACTS_PANEL_ICON_ONLY_BREAKPOINT = 545;
export const ARTIFACTS_PANEL_DROPDOWN_BREAKPOINT = 420;
export const NARROW_CHAT_PAGE_BREAKPOINT = 800;

// Component IDs used for measuring widths

export const PROJECT_LAYOUT_ID = "project-layout";
export const CHAT_PAGE_ID = "chat-page";
