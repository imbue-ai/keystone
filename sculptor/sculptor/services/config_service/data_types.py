from typing import Annotated
from typing import Any

from pydantic import ConfigDict
from pydantic import Field
from pydantic import Tag

from imbue_core.pydantic_serialization import FrozenModel
from imbue_core.pydantic_serialization import SerializableModel
from imbue_core.pydantic_serialization import build_discriminator
from imbue_core.secrets_utils import Secret

# We pretend to be Claude Code when initiating the OAuth flow.
CLAUDE_CODE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
REFRESH_TOKEN_EXPIRY_BUFFER_SECONDS = 60 * 60


class AnthropicApiKey(SerializableModel):
    object_type: str = "AnthropicApiKey"
    anthropic_api_key: Secret
    # This field was added later, and we may have users who logged in via OAuth before it was added.
    # Keys generated from OAuth are more restricted, so defaulting to True makes more sense.
    generated_from_oauth: bool = True


class AWSBedrockApiKey(SerializableModel):
    object_type: str = "AWSBedrockApiKey"
    bedrock_api_key: Secret


_MASKED_REFRESH_TOKEN = (
    "sk-ant-ort01-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx-xxxxxxxx"
)


class ClaudeOauthCredentials(SerializableModel):
    object_type: str = "ClaudeOauthCredentials"
    access_token: Secret
    refresh_token: Secret
    expires_at_unix_ms: int
    scopes: list[str]
    subscription_type: str

    def convert_to_claude_code_credentials_json_section(self, mask_refresh_token: bool = True) -> dict[str, Any]:
        return {
            "claudeAiOauth": {
                "accessToken": self.access_token.unwrap(),
                "refreshToken": (self.refresh_token.unwrap() if not mask_refresh_token else _MASKED_REFRESH_TOKEN),
                "expiresAt": self.expires_at_unix_ms,
                "scopes": self.scopes,
                "subscriptionType": self.subscription_type,
            },
        }


class OpenAIApiKey(SerializableModel):
    object_type: str = "OpenAIApiKey"
    openai_api_key: Secret
    # This field was added later, and we may have users who logged in via OAuth before it was added.
    # Keys generated from OAuth are more restricted, so defaulting to True makes more sense.
    generated_from_oauth: bool = True


AnthropicCredentials = Annotated[
    Annotated[AnthropicApiKey, Tag("AnthropicApiKey")]
    | Annotated[AWSBedrockApiKey, Tag("AWSBedrockApiKey")]
    | Annotated[ClaudeOauthCredentials, Tag("ClaudeOauthCredentials")],
    build_discriminator(),
]


class Credentials(SerializableModel):
    anthropic: AnthropicCredentials | None = Field(default=None)
    openai: OpenAIApiKey | None = Field(default=None)

    @property
    def is_set(self):
        return self.anthropic is not None or self.openai is not None


class TokenResponse(FrozenModel):
    model_config = ConfigDict(extra="ignore")
    access_token: str
    refresh_token: str
    expires_in: int  # seconds
    scope: str


class GlobalConfiguration(SerializableModel):
    """Global (user-level) configuration settings."""

    credentials: Credentials = Field(default_factory=Credentials)
