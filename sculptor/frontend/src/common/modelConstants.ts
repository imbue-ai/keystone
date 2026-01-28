import { LlmModel } from "~/api";

const modelNames: Record<LlmModel, { short: string; long: string }> = {
  [LlmModel.CLAUDE_4_OPUS]: { short: "Opus", long: "Claude 4.5 Opus" },
  [LlmModel.CLAUDE_4_SONNET]: { short: "Sonnet", long: "Claude 4.5 Sonnet" },
  [LlmModel.CLAUDE_4_HAIKU]: { short: "Haiku", long: "Claude 4.5 Haiku" },
  [LlmModel.GPT_5_1_CODEX]: { short: "Codex", long: "Codex (Beta)" },
  [LlmModel.GPT_5_1_CODEX_MINI]: { short: "Codex Mini", long: "Codex Mini (Beta)" },
  [LlmModel.GPT_5_1]: { short: "GPT 5.1", long: "GPT 5.1 (Beta)" },
  [LlmModel.GPT_5_2]: { short: "GPT 5.2", long: "GPT 5.2 (Beta)" },
} as const;

export const codexModels: Array<LlmModel> = [
  LlmModel.GPT_5_1_CODEX,
  LlmModel.GPT_5_1_CODEX_MINI,
  LlmModel.GPT_5_1,
  LlmModel.GPT_5_2,
];

export const getModelShortName = (model: LlmModel): string => modelNames[model]?.short || "Unknown";
export const getModelLongName = (model: LlmModel): string => modelNames[model]?.long || "Unknown";
