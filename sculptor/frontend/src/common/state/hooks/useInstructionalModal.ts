import { useAtom } from "jotai";

import { UserConfigField } from "~/api";

import { instructionalModalOpenAtom } from "../atoms/instructionalModal.ts";
import { useUserConfig } from "./useUserConfig.ts";

type InstructionalModalControls = {
  isInstructionalModalOpen: boolean;
  setIsInstructionalModalOpen: (isOpen: boolean) => void;
  showInstructionalModal: () => void;
  hideInstructionalModal: () => void;
};

export const useInstructionalModal = (): InstructionalModalControls => {
  const [isInstructionalModalOpen, setIsInstructionalModalOpen] = useAtom(instructionalModalOpenAtom);
  const { updateField } = useUserConfig();

  const hideInstructionalModal = async (): Promise<void> => {
    setIsInstructionalModalOpen(false);
    try {
      await updateField(UserConfigField.HAS_SEEN_PAIRING_MODE_MODAL, true);
    } catch (error) {
      console.error("Failed to update user config:", error);
    }
  };

  return {
    isInstructionalModalOpen,
    setIsInstructionalModalOpen,
    showInstructionalModal: () => setIsInstructionalModalOpen(true),
    hideInstructionalModal,
  };
};
