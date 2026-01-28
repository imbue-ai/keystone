import { AlertDialog, Button, Flex, Text, TextField } from "@radix-ui/themes";
import { type ReactElement, useEffect, useState } from "react";

import { openaiCredentialsExists, saveOpenaiKey } from "~/api";

import type { ToastContent } from "./Toast";
import { ToastType } from "./Toast";
import { Toast } from "./Toast";

const OpenAIAccessStatus = {
  NOT_CONFIGURED: "NOT_CONFIGURED",
  INPUTTING_API_KEY: "INPUTTING_API_KEY",
  CONFIGURED: "CONFIGURED",
} as const;

type OpenAIAccessStatus = (typeof OpenAIAccessStatus)[keyof typeof OpenAIAccessStatus];

type OpenAIAuthButtonProps = {
  onAuthStatusChange?: (isConfigured: boolean) => void;
  buttonVariant?: "solid" | "soft" | "outline" | "ghost";
};

export const OpenAIAuthButton = ({ onAuthStatusChange, buttonVariant }: OpenAIAuthButtonProps): ReactElement => {
  const [openAIAccessStatus, setOpenAIAccessStatus] = useState<OpenAIAccessStatus>(OpenAIAccessStatus.NOT_CONFIGURED);
  const [apiKeyInModal, setApiKeyInModal] = useState("");
  const [toast, setToast] = useState<ToastContent | null>(null);

  // Check initial auth status
  useEffect(() => {
    const checkInitialAuthStatus = async (): Promise<void> => {
      try {
        const { data: doesCredsExist } = await openaiCredentialsExists({ meta: { skipWsAck: true } });
        if (doesCredsExist) {
          setOpenAIAccessStatus(OpenAIAccessStatus.CONFIGURED);
          onAuthStatusChange?.(true);
        }
      } catch (err) {
        console.error("Failed to check initial OpenAI credential status:", err);
      }
    };
    checkInitialAuthStatus();
  }, [onAuthStatusChange]);

  // Notify parent of auth status changes
  useEffect(() => {
    onAuthStatusChange?.(openAIAccessStatus === OpenAIAccessStatus.CONFIGURED);
  }, [openAIAccessStatus, onAuthStatusChange]);

  const handleApiKeyModalSubmit = async (): Promise<void> => {
    if (apiKeyInModal) {
      try {
        await saveOpenaiKey({
          body: apiKeyInModal,
          meta: { skipWsAck: true },
        });
        setApiKeyInModal("");
        setOpenAIAccessStatus(OpenAIAccessStatus.CONFIGURED);
        console.log("OpenAI API key saved successfully");
      } catch (err) {
        console.error("Failed to save OpenAI API key:", err);
        setToast({ title: `Failed to save OpenAI API key. ${err}`, type: ToastType.ERROR });
      }
    }
  };

  return (
    <>
      <Button
        onClick={() => setOpenAIAccessStatus(OpenAIAccessStatus.INPUTTING_API_KEY)}
        variant={buttonVariant ?? "soft"}
      >
        Manage OpenAI Access
      </Button>

      {/* API Key Input Modal */}
      <AlertDialog.Root
        open={openAIAccessStatus === OpenAIAccessStatus.INPUTTING_API_KEY}
        onOpenChange={(open) => {
          setOpenAIAccessStatus(open ? OpenAIAccessStatus.INPUTTING_API_KEY : OpenAIAccessStatus.NOT_CONFIGURED);
        }}
      >
        <AlertDialog.Content maxWidth="450px">
          <AlertDialog.Title>OpenAI API Key</AlertDialog.Title>
          <Flex direction="column" gap="3" mt="4">
            <Text size="2">
              Enter your OpenAI API key to enable Codex and OpenAI-powered features. You can get an API key from the
              OpenAI platform.
            </Text>
            <TextField.Root
              placeholder="sk-..."
              value={apiKeyInModal}
              onChange={(e) => setApiKeyInModal(e.target.value)}
              type="password"
            />
          </Flex>

          <Flex gap="3" mt="4" justify="end">
            <AlertDialog.Cancel>
              <Button variant="soft" color="gray">
                Cancel
              </Button>
            </AlertDialog.Cancel>
            <AlertDialog.Action>
              <Button
                variant="solid"
                onClick={handleApiKeyModalSubmit}
                disabled={!apiKeyInModal || !apiKeyInModal.startsWith("sk-")}
              >
                Save Key
              </Button>
            </AlertDialog.Action>
          </Flex>
        </AlertDialog.Content>
      </AlertDialog.Root>
      <Toast open={!!toast} onOpenChange={(open) => !open && setToast(null)} title={toast?.title} type={toast?.type} />
    </>
  );
};
