import * as Dialog from "@radix-ui/react-dialog";
import { Flex, VisuallyHidden } from "@radix-ui/themes";
import type { ReactElement } from "react";

import { useInstructionalModal } from "~/common/state/hooks/useInstructionalModal.ts";

import styles from "./InstructionalModal.module.scss";

export const InstructionalModal = (): ReactElement => {
  const { isInstructionalModalOpen, hideInstructionalModal } = useInstructionalModal();
  return (
    <>
      <Dialog.Root
        open={isInstructionalModalOpen}
        onOpenChange={(o) => {
          if (!o) {
            hideInstructionalModal();
          }
        }}
      >
        <VisuallyHidden>
          <Dialog.Title>How to use Pairing Mode</Dialog.Title>
        </VisuallyHidden>
        <Dialog.Overlay className={styles.overlay} />
        <Dialog.Content className={styles.modalContainer}>
          <Flex direction="column" p="4">
            <iframe
              src="https://www.loom.com/embed/1b02a925be42431da1721597687f7065?sid=48b78bdf-3039-42e0-939e-125fb2fe825e"
              frameBorder="0"
              allowFullScreen={true}
              style={{ position: "absolute", top: 0, left: 0, width: "100%", height: "100%" }}
            />
          </Flex>
        </Dialog.Content>
      </Dialog.Root>
    </>
  );
};
