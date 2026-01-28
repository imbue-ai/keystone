import type { ScoutOutput } from "~/api/types.gen";
import { isScoutOutput } from "~/common/Guards";

import type {
  CheckOutputWithSource,
  NewCheckOutputsData,
  NewScoutOutputsData,
  ScoutOutputWithSource,
} from "../../Types";

export const filterScoutOutputsFromCheckOutputs = (
  checkOutputs: NewCheckOutputsData | undefined,
): NewScoutOutputsData => {
  console.log("scout checkOutputs", checkOutputs);
  const checkOutputsByMessageId = checkOutputs?.checkOutputsByMessageId ?? {};
  const scoutOutputsByMessageId: Record<string, Array<ScoutOutputWithSource>> = {};

  for (const [messageId, checkOutputArray] of Object.entries(checkOutputsByMessageId)) {
    const scoutOutputs = checkOutputArray.filter((item): item is CheckOutputWithSource & { output: ScoutOutput } =>
      isScoutOutput(item.output),
    );
    if (scoutOutputs.length > 0) {
      scoutOutputsByMessageId[messageId] = scoutOutputs.map((s) => ({
        output: s.output,
        runId: s.runId,
        checkName: s.checkName,
      }));
    }
  }
  return { scoutOutputsByMessageId };
};

export const getScoutOutputForMessage = (
  scoutOutputsData: NewScoutOutputsData | undefined,
  messageId: string,
): Array<ScoutOutputWithSource> => {
  if (!scoutOutputsData?.scoutOutputsByMessageId?.[messageId]) {
    return [];
  }

  const newScoutOutputs = scoutOutputsData.scoutOutputsByMessageId[messageId];
  return newScoutOutputs;
};

export const getAllScoutOutputsForMessages = (
  scoutOutputsData: NewScoutOutputsData | undefined,
  messageIds: Array<string>,
): Array<ScoutOutputWithSource> => {
  const allScoutOutputs: Array<ScoutOutputWithSource> = [];

  for (let i = messageIds.length - 1; i >= 0; i--) {
    const scoutOutputs = getScoutOutputForMessage(scoutOutputsData, messageIds[i]);
    allScoutOutputs.push(...scoutOutputs);
  }

  return allScoutOutputs;
};
