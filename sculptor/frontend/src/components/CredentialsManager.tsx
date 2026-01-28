import { Badge, Box, Card, Flex, Heading, Text } from "@radix-ui/themes";
import { type ReactElement, useEffect, useState } from "react";

import { AnthropicAuthButton } from "./AnthropicAuthButton";
import { OpenAIAuthButton } from "./OpenAIAuthButton";

type CredentialsManagerProps = {
  onCredentialsChange?: (hasAnyCredentials: boolean) => void;
  compact?: boolean;
};

export const CredentialsManager = ({ onCredentialsChange, compact = false }: CredentialsManagerProps): ReactElement => {
  const [hasAnthropicCredentials, setHasAnthropicCredentials] = useState(false);
  const [hasOpenAICredentials, setHasOpenAICredentials] = useState(false);

  useEffect(() => {
    onCredentialsChange?.(hasAnthropicCredentials || hasOpenAICredentials);
  }, [hasAnthropicCredentials, hasOpenAICredentials, onCredentialsChange]);

  if (compact) {
    return (
      <Flex gap="3" direction="column">
        <Flex gap="2" align="center">
          <AnthropicAuthButton onAuthStatusChange={setHasAnthropicCredentials} buttonVariant="outline" />
          {hasAnthropicCredentials && <Badge color="green">Configured</Badge>}
        </Flex>
        <Flex gap="2" align="center">
          <OpenAIAuthButton onAuthStatusChange={setHasOpenAICredentials} buttonVariant="outline" />
          {hasOpenAICredentials && <Badge color="green">Configured</Badge>}
        </Flex>
      </Flex>
    );
  }

  return (
    <Box>
      <Heading size="5" mb="4">
        API Credentials
      </Heading>
      <Text size="2" mb="4" color="gray">
        Configure at least one AI provider to enable Sculptor&apos;s features. Anthropic is recommended for Claude Code
        agents, while OpenAI enables Codex features.
      </Text>

      <Flex gap="4" direction="column">
        <Card>
          <Flex justify="between" align="center">
            <Box>
              <Heading size="3">Anthropic (Claude)</Heading>
              <Text size="2" color="gray" mt="1">
                Powers Claude Code agents with advanced reasoning capabilities
              </Text>
            </Box>
            <Flex gap="2" align="center">
              {hasAnthropicCredentials && <Badge color="green">Configured</Badge>}
              <AnthropicAuthButton onAuthStatusChange={setHasAnthropicCredentials} />
            </Flex>
          </Flex>
        </Card>

        <Card>
          <Flex justify="between" align="center">
            <Box>
              <Heading size="3">OpenAI</Heading>
              <Text size="2" color="gray" mt="1">
                Powers Codex agents and GPT-based features
              </Text>
            </Box>
            <Flex gap="2" align="center">
              {hasOpenAICredentials && <Badge color="green">Configured</Badge>}
              <OpenAIAuthButton onAuthStatusChange={setHasOpenAICredentials} />
            </Flex>
          </Flex>
        </Card>
      </Flex>

      {!hasAnthropicCredentials && !hasOpenAICredentials && (
        <Text size="2" color="red" mt="3">
          ⚠️ Please configure at least one AI provider to continue
        </Text>
      )}
    </Box>
  );
};
