import json
from pathlib import Path

from sculptor.services.config_service.data_types import AWSBedrockApiKey
from sculptor.services.config_service.data_types import AnthropicApiKey
from sculptor.services.config_service.data_types import ClaudeOauthCredentials
from sculptor.services.config_service.data_types import Credentials
from sculptor.services.config_service.data_types import OpenAIApiKey


def populate_credentials_file(path: Path, credentials: Credentials) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    json_dict = credentials.model_dump()
    if json_dict.get("anthropic") is not None:
        anthropic_credentials = credentials.anthropic
        if json_dict["anthropic"]["object_type"] == "AnthropicApiKey":
            assert isinstance(anthropic_credentials, AnthropicApiKey)
            json_dict["anthropic"]["anthropic_api_key"] = anthropic_credentials.anthropic_api_key.unwrap()
        elif json_dict["anthropic"]["object_type"] == "AWSBedrockApiKey":
            assert isinstance(anthropic_credentials, AWSBedrockApiKey)
            json_dict["anthropic"]["bedrock_api_key"] = anthropic_credentials.bedrock_api_key.unwrap()
        elif json_dict["anthropic"]["object_type"] == "ClaudeOauthCredentials":
            assert isinstance(anthropic_credentials, ClaudeOauthCredentials)
            json_dict["anthropic"]["refresh_token"] = anthropic_credentials.refresh_token.unwrap()
            json_dict["anthropic"]["access_token"] = anthropic_credentials.access_token.unwrap()
        else:
            raise ValueError(f"Unknown object type: {json_dict['anthropic']['object_type']}")

    if json_dict.get("openai") is not None:
        if json_dict["openai"]["object_type"] == "OpenAIApiKey":
            assert isinstance(credentials.openai, OpenAIApiKey)
            json_dict["openai"]["openai_api_key"] = credentials.openai.openai_api_key.unwrap()
    path.write_text(json.dumps(json_dict))
