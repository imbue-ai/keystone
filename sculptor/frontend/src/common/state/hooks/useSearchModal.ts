import { useAtom } from "jotai";

import { searchModalOpenAtom } from "../atoms/searchModal.ts";

type SearchModalControls = {
  isSearchModalOpen: boolean;
  setIsSearchModalOpen: (isOpen: boolean) => void;
  showSearchModal: () => void;
  hideSearchModal: () => void;
  toggleSearchModal: () => void;
};

export const useSearchModal = (): SearchModalControls => {
  const [isSearchModalOpen, setIsSearchModalOpen] = useAtom(searchModalOpenAtom);

  return {
    isSearchModalOpen,
    setIsSearchModalOpen,
    showSearchModal: () => setIsSearchModalOpen(true),
    hideSearchModal: () => setIsSearchModalOpen(false),
    toggleSearchModal: () => setIsSearchModalOpen((prev) => !prev),
  };
};
