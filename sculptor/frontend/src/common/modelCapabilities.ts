import { LlmModel } from "~/api";

type ModelCapabilities = {
  supportsSystemPrompt: boolean;
  supportsFileAttachments: boolean;
};

const MODEL_CAPABILITIES: Record<LlmModel, ModelCapabilities> = {
  [LlmModel.GPT_5_1]: {
    supportsSystemPrompt: false,
    supportsFileAttachments: false,
  },
  [LlmModel.GPT_5_2]: {
    supportsSystemPrompt: false,
    supportsFileAttachments: false,
  },
  [LlmModel.GPT_5_1_CODEX]: {
    supportsSystemPrompt: false,
    supportsFileAttachments: false,
  },
  [LlmModel.GPT_5_1_CODEX_MINI]: {
    supportsSystemPrompt: false,
    supportsFileAttachments: false,
  },
  [LlmModel.CLAUDE_4_OPUS]: {
    supportsSystemPrompt: true,
    supportsFileAttachments: true,
  },
  [LlmModel.CLAUDE_4_SONNET]: {
    supportsSystemPrompt: true,
    supportsFileAttachments: true,
  },
  [LlmModel.CLAUDE_4_HAIKU]: {
    supportsSystemPrompt: true,
    supportsFileAttachments: true,
  },
};

export const getModelCapabilities = (model: LlmModel): ModelCapabilities => {
  return MODEL_CAPABILITIES[model];
};
