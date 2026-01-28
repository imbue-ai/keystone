import { AlertDialog, Button, Dialog, Flex, Link, Spinner, Text, TextField } from "@radix-ui/themes";
import { CopyIcon } from "lucide-react";
import { type ReactElement, useEffect, useState } from "react";

import { AnthropicAccountType } from "../api";
import {
  anthropicCredentialsExists,
  cancelAnthropicOauth,
  ElementIds,
  saveApiKey,
  saveBedrockKey,
  startAnthropicOauth,
} from "../api";
import styles from "./AnthropicAuthButton.module.scss";
import type { ToastContent } from "./Toast";
import { ToastType } from "./Toast";
import { Toast } from "./Toast";
import { TooltipIconButton } from "./TooltipIconButton";

const AnthropicAccessStatus = {
  NOT_CONFIGURED: "NOT_CONFIGURED",
  CHOOSING_METHOD: "CHOOSING_METHOD",
  INPUTTING_API_KEY: "INPUTTING_API_KEY",
  INPUTTING_BEDROCK_KEY: "INPUTTING_BEDROCK_KEY",
  OAUTH_IN_PROGRESS: "OAUTH_IN_PROGRESS",
  CONFIGURED: "CONFIGURED",
} as const;

type AnthropicAccessStatus = (typeof AnthropicAccessStatus)[keyof typeof AnthropicAccessStatus];

type AnthropicAuthButtonProps = {
  onAuthStatusChange?: (isConfigured: boolean) => void;
  buttonVariant?: "solid" | "soft" | "outline" | "ghost";
};

export const AnthropicAuthButton = ({ onAuthStatusChange, buttonVariant }: AnthropicAuthButtonProps): ReactElement => {
  const [anthropicAccessStatus, setAnthropicAccessStatus] = useState<AnthropicAccessStatus>(
    AnthropicAccessStatus.NOT_CONFIGURED,
  );
  // eslint-disable-next-line react/hook-use-state
  const [, setCredentialsPollIntervalId] = useState<NodeJS.Timeout | undefined>(undefined);
  const [apiKeyInModal, setApiKeyInModal] = useState("");
  const [bedrockKeyInModal, setBedrockKeyInModal] = useState("");
  const [toast, setToast] = useState<ToastContent | null>(null);
  const [oauthUrl, setOauthUrl] = useState<string | undefined>(undefined);

  // Check initial auth status
  useEffect(() => {
    const checkInitialAuthStatus = async (): Promise<void> => {
      try {
        const { data: doesCredsExist } = await anthropicCredentialsExists({ meta: { skipWsAck: true } });
        if (doesCredsExist) {
          setAnthropicAccessStatus(AnthropicAccessStatus.CONFIGURED);
          onAuthStatusChange?.(true);
        }
      } catch (err) {
        console.error("Failed to check initial credential status:", err);
      }
    };
    checkInitialAuthStatus();
  }, [onAuthStatusChange]);

  // Notify parent of auth status changes
  useEffect(() => {
    onAuthStatusChange?.(anthropicAccessStatus === AnthropicAccessStatus.CONFIGURED);
  }, [anthropicAccessStatus, onAuthStatusChange]);

  const stopCredentialsPoll = (): void => {
    setCredentialsPollIntervalId((intervalId) => {
      if (intervalId !== undefined) {
        clearInterval(intervalId);
        return undefined;
      }
      return intervalId;
    });
  };

  const handleCopyUrl = (): void => {
    if (oauthUrl) {
      navigator.clipboard
        ?.writeText(oauthUrl)
        .then(() => {
          setToast({ title: "URL copied to clipboard", type: ToastType.SUCCESS });
        })
        .catch((err) => {
          console.error("Failed to copy URL to clipboard:", err);
          setToast({ title: "Failed to copy URL - please select and copy the text manually", type: ToastType.ERROR });
        });
    }
  };

  const startAnthropicOauthFlow = async (accountType: AnthropicAccountType): Promise<void> => {
    const response = await startAnthropicOauth({ query: { account_type: accountType }, meta: { skipWsAck: true } });
    const url: string = response.data as unknown as string;
    setOauthUrl(url);
    const windowOpenSuccess = window.open(url);
    if (!windowOpenSuccess) {
      console.error("Failed to open OAuth window. It may have been blocked by a popup blocker. URL: " + url);
    }
    setAnthropicAccessStatus(AnthropicAccessStatus.OAUTH_IN_PROGRESS);
    const intervalId = setInterval(async () => {
      try {
        const response = await anthropicCredentialsExists({ meta: { skipWsAck: true } });
        if (response.data) {
          setAnthropicAccessStatus(AnthropicAccessStatus.CONFIGURED);
          stopCredentialsPoll();
        }
      } catch (err) {
        console.error("Failed to check credential status:", err);
      }
    }, 1000);
    setCredentialsPollIntervalId(intervalId);
    // Clear the interval when hot reloading
    import.meta.hot?.dispose(() => {
      stopCredentialsPoll();
    });
  };

  const cancelAnthropicOauthFlow = async (): Promise<void> => {
    setAnthropicAccessStatus(AnthropicAccessStatus.CHOOSING_METHOD);
    stopCredentialsPoll();
    setOauthUrl(undefined); // Clear the URL when cancelling
    await cancelAnthropicOauth({ meta: { skipWsAck: true } });
  };

  const handleApiKeyModalSubmit = async (): Promise<void> => {
    if (apiKeyInModal) {
      try {
        await saveApiKey({
          body: apiKeyInModal,
          meta: { skipWsAck: true },
        });
        setApiKeyInModal("");
        setAnthropicAccessStatus(AnthropicAccessStatus.CONFIGURED);
        console.log("API key saved successfully");
      } catch (err) {
        console.error("Failed to save API key:", err);
        setToast({ title: `Failed to save API key. ${err}`, type: ToastType.ERROR });
      }
    }
  };

  const handleBedrockKeyModalSubmit = async (): Promise<void> => {
    if (bedrockKeyInModal) {
      try {
        await saveBedrockKey({
          body: bedrockKeyInModal,
          meta: { skipWsAck: true },
        });
        setBedrockKeyInModal("");
        setAnthropicAccessStatus(AnthropicAccessStatus.CONFIGURED);
        console.log("AWS Bedrock key saved successfully");
      } catch (err) {
        console.error("Failed to save AWS Bedrock key:", err);
        setToast({ title: `Failed to save AWS Bedrock key. ${err}`, type: ToastType.ERROR });
      }
    }
  };

  // Clean up interval on unmount
  useEffect(() => {
    return (): void => {
      stopCredentialsPoll();
    };
  }, []);

  return (
    <>
      <Button
        onClick={() => setAnthropicAccessStatus(AnthropicAccessStatus.CHOOSING_METHOD)}
        data-testid={ElementIds.ONBOARDING_ANTHROPIC_ACCESS_MODAL_OPEN_BUTTON}
        variant={buttonVariant ?? "soft"}
      >
        Manage Anthropic Access
      </Button>
      {/* Method Selection Modal */}
      <Dialog.Root
        open={anthropicAccessStatus === AnthropicAccessStatus.CHOOSING_METHOD}
        onOpenChange={(open) => {
          if (!open) {
            setAnthropicAccessStatus(AnthropicAccessStatus.NOT_CONFIGURED);
          }
        }}
      >
        <Dialog.Content maxWidth="400px" data-testid={ElementIds.ONBOARDING_ANTHROPIC_ACCESS_MODAL}>
          <Dialog.Title>Manage Anthropic access</Dialog.Title>
          <Flex direction="column" align="center" gapY="3">
            <Text>Select your preferred method of managing Sculptor access to use your Anthropic account</Text>
            <Button
              variant="soft"
              onClick={() => startAnthropicOauthFlow(AnthropicAccountType.CLAUDE)}
              style={{ width: "100%" }}
            >
              Claude Max/Team/Pro account (beta)
            </Button>
            <Button
              variant="soft"
              onClick={() => startAnthropicOauthFlow(AnthropicAccountType.ANTHROPIC_CONSOLE)}
              style={{ width: "100%" }}
            >
              Claude Console account
            </Button>
            <Button
              variant="soft"
              onClick={() => setAnthropicAccessStatus(AnthropicAccessStatus.INPUTTING_API_KEY)}
              data-testid={ElementIds.ONBOARDING_API_KEY_MODAL_OPEN_BUTTON}
              style={{ width: "100%" }}
            >
              API key
            </Button>
            <Button
              variant="soft"
              onClick={() => setAnthropicAccessStatus(AnthropicAccessStatus.INPUTTING_BEDROCK_KEY)}
              style={{ width: "100%" }}
            >
              AWS Bedrock key (beta)
            </Button>
          </Flex>
        </Dialog.Content>
      </Dialog.Root>

      {/* OAuth In Progress Modal */}
      <AlertDialog.Root
        open={anthropicAccessStatus === AnthropicAccessStatus.OAUTH_IN_PROGRESS}
        onOpenChange={async (open) => {
          if (open) {
            setAnthropicAccessStatus(AnthropicAccessStatus.OAUTH_IN_PROGRESS);
          } else {
            await cancelAnthropicOauthFlow();
          }
        }}
      >
        <AlertDialog.Content maxWidth="500px">
          <AlertDialog.Title>Signing In</AlertDialog.Title>
          <Flex direction="column" align="center" gapY="3" mt="4">
            <Text>Please continue in the Anthropic sign-in page</Text>
            {oauthUrl && (
              <>
                <Text size="2" color="gray">
                  Or, open this URL yourself:
                </Text>
                <Flex direction="row" align="center" gap="2" style={{ width: "100%" }}>
                  <TooltipIconButton tooltipText="Copy URL" onClick={handleCopyUrl} size="2">
                    <CopyIcon size={16} />
                  </TooltipIconButton>
                  <Text size="1" className={styles.oauthUrlContainer}>
                    {oauthUrl}
                  </Text>
                </Flex>
              </>
            )}
            <Spinner size="3" />
          </Flex>
          <Flex gap="3" mt="4" justify="end">
            <AlertDialog.Cancel>
              <Button variant="soft" color="gray">
                Cancel
              </Button>
            </AlertDialog.Cancel>
          </Flex>
        </AlertDialog.Content>
      </AlertDialog.Root>

      {/* API Key Input Modal */}
      <AlertDialog.Root
        open={anthropicAccessStatus === AnthropicAccessStatus.INPUTTING_API_KEY}
        onOpenChange={(open) => {
          setAnthropicAccessStatus(
            open ? AnthropicAccessStatus.INPUTTING_API_KEY : AnthropicAccessStatus.CHOOSING_METHOD,
          );
        }}
      >
        <AlertDialog.Content maxWidth="450px" data-testid={ElementIds.ONBOARDING_API_KEY_MODAL}>
          <AlertDialog.Title>Anthropic API Key</AlertDialog.Title>
          <Flex direction="column" gap="3" mt="4">
            <TextField.Root
              placeholder="Anthropic API Key"
              value={apiKeyInModal}
              onChange={(e) => setApiKeyInModal(e.target.value)}
              type="password"
              data-testid={ElementIds.ONBOARDING_API_KEY_INPUT}
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
                disabled={!apiKeyInModal}
                data-testid={ElementIds.ONBOARDING_API_KEY_SUBMIT}
              >
                Save Key
              </Button>
            </AlertDialog.Action>
          </Flex>
        </AlertDialog.Content>
      </AlertDialog.Root>

      {/* AWS Bedrock Key Input Modal */}
      <AlertDialog.Root
        open={anthropicAccessStatus === AnthropicAccessStatus.INPUTTING_BEDROCK_KEY}
        onOpenChange={(open) => {
          setAnthropicAccessStatus(
            open ? AnthropicAccessStatus.INPUTTING_BEDROCK_KEY : AnthropicAccessStatus.CHOOSING_METHOD,
          );
        }}
      >
        <AlertDialog.Content maxWidth="450px">
          <AlertDialog.Title>AWS Bedrock Key (beta)</AlertDialog.Title>
          <Flex direction="column" gap="3" mt="4">
            <Text>
              Currently, only long term Bedrock API tokens are supported. See here for{" "}
              <Link
                target="_blank"
                href="https://docs.aws.amazon.com/bedrock/latest/userguide/getting-started-api-keys.html"
              >
                details
              </Link>
            </Text>
            <TextField.Root
              placeholder="AWS Bedrock Bearer Token"
              value={bedrockKeyInModal}
              onChange={(e) => setBedrockKeyInModal(e.target.value)}
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
              <Button variant="solid" onClick={handleBedrockKeyModalSubmit} disabled={!bedrockKeyInModal}>
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
