import json
import re
import time
from typing import TypeVar
from typing import assert_never

import anthropic
import openai
from loguru import logger
from pydantic import BaseModel
from pydantic import ValidationError

from imbue_core.agents.llm_apis.anthropic_api import prepend_claude_code_system_prompt
from sculptor.server.llm_clients import AnthropicClient
from sculptor.server.llm_clients import LLMAPIError
from sculptor.server.llm_clients import LLMValidationError
from sculptor.server.llm_clients import OpenAIClient
from sculptor.services.config_service.data_types import AWSBedrockApiKey
from sculptor.services.config_service.data_types import AnthropicApiKey
from sculptor.services.config_service.data_types import AnthropicCredentials
from sculptor.services.config_service.data_types import ClaudeOauthCredentials
from sculptor.services.config_service.data_types import Credentials
from sculptor.services.config_service.data_types import OpenAIApiKey

T = TypeVar("T", bound=BaseModel)

DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 1.0  # seconds


def get_anthropic_client(credentials: AnthropicCredentials) -> AnthropicClient:
    """Get an Anthropic client using the credentials from the service."""
    match credentials:
        case AnthropicApiKey(anthropic_api_key=anthropic_api_key):
            return AnthropicClient(client=anthropic.Anthropic(api_key=anthropic_api_key.unwrap()))
        case ClaudeOauthCredentials(access_token=access_token):
            return AnthropicClient(
                client=anthropic.Anthropic(
                    auth_token=access_token.unwrap(), default_headers={"anthropic-beta": "oauth-2025-04-20"}
                )
            )
        case AWSBedrockApiKey(bedrock_api_key=bedrock_api_key):
            return AnthropicClient(
                client=anthropic.Anthropic(
                    api_key=bedrock_api_key.unwrap(),
                    default_headers={"anthropic-beta": "bedrock-2025-04-20"},
                )
            )

        case _ as unreachable:
            # TODO: get pyre to understand matching on annotated unions
            assert_never(unreachable)  # pyre-fixme[6]


def get_openai_client(credentials: OpenAIApiKey) -> OpenAIClient:
    return OpenAIClient(client=openai.OpenAI(api_key=credentials.openai_api_key.unwrap()))


def get_llm_response(
    prompt: str,
    credentials: Credentials,
    model: str | None = None,
    max_tokens: int = 4000,
    temperature: float = 0.7,
    system_prompt: str | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> str:
    """
    Get a response from an LLM with a simple string prompt.

    Args:
        credentials: LLM credentials
        prompt: The user prompt to send to the LLM
        model: The model name to use (defaults to Claude 3.7 Sonnet)
        max_tokens: Maximum tokens in the response
        temperature: Sampling temperature (0.0 to 1.0)
        system_prompt: Optional system prompt to set context
        max_retries: Maximum number of retries on API errors
    Returns:
        The LLM's response as a string

    Raises:
        LLMAPIError: If the API call fails after retries
        LLMError: For other configuration issues
    """
    if credentials.anthropic is not None:
        client = get_anthropic_client(credentials=credentials.anthropic)
    elif credentials.openai is not None:
        client = get_openai_client(credentials=credentials.openai)
    else:
        raise LLMAPIError("Found no valid credentials")

    messages = [{"role": "user", "content": prompt}]
    system = prepend_claude_code_system_prompt(system_prompt)

    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            logger.debug("Making LLM call (attempt {}/{})", attempt + 1, max_retries + 1)
            return client.get_response(
                max_tokens=max_tokens, temperature=temperature, messages=messages, system=system, model=model
            )
        except (anthropic.APIError, openai.APIError) as e:
            last_exception = e
            logger.debug("LLM API error on attempt {}: {}", attempt + 1, e)
            if attempt < max_retries:
                time.sleep(DEFAULT_RETRY_DELAY * (2**attempt))  # Exponential backoff
            continue
        except Exception as e:
            raise LLMAPIError(f"Unexpected error during LLM call: {e}") from e

    raise LLMAPIError(f"LLM API call failed after {max_retries + 1} attempts. Last error: {last_exception}")


class InternalValidationError(Exception):
    pass


def get_structured_llm_response(
    prompt: str,
    output_type: type[T],
    credentials: Credentials,
    model: str | None = None,
    max_tokens: int = 4000,
    temperature: float = 0.7,
    system_prompt: str | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    validation_retries: int = 3,
) -> T:
    """
    Get a structured response from an LLM that conforms to a Pydantic model.

    Args:
        prompt: The user prompt to send to the LLM
        output_type: Pydantic model class that defines the expected structure
        credentials: LLM credentials
        model: The model name to use (defaults to None)
        max_tokens: Maximum tokens in the response
        temperature: Sampling temperature (0.0 to 1.0)
        system_prompt: Optional system prompt to set context
        max_retries: Maximum number of retries on API errors
        validation_retries: Maximum number of retries when JSON parsing/validation fails

    Returns:
        An instance of output_type with the LLM's structured response

    Raises:
        LLMValidationError: If validation fails after retries
        LLMAPIError: If the API call fails after retries
        LLMError: For other configuration issues
    """
    schema = output_type.model_json_schema()

    enhanced_prompt = f"""
<Formatting Instructions>
    Please respond with valid JSON that matches this exact schema:

    <Schema>
        {json.dumps(schema, indent=2)}
    </Schema>

    Important:
    - Return ONLY the JSON object, no additional text or formatting
    - Ensure all required fields are included
    - Follow the exact data types specified in the schema
    - Do not include any markdown formatting or code blocks
</Formatting Instructions>

<Prompt>
    {prompt}
</Prompt>
"""

    # Retry validation logic
    last_validation_error = None
    for validation_attempt in range(validation_retries + 1):
        try:
            # Get raw response from LLM
            response_text = get_llm_response(
                prompt=enhanced_prompt,
                credentials=credentials,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system_prompt=system_prompt,
                max_retries=max_retries,
            )

            # Clean the response (remove potential markdown formatting)
            cleaned_response = re.sub(r"^```(?:json)?\s*", "", response_text.strip(), flags=re.MULTILINE)
            cleaned_response = re.sub(r"```\s*$", "", cleaned_response, flags=re.MULTILINE).strip()

            try:
                parsed_data = json.loads(cleaned_response)
            except json.JSONDecodeError as e:
                raise InternalValidationError(f"Invalid JSON for {output_type}: {e}")

            return output_type.model_validate(parsed_data)

        except (InternalValidationError, ValidationError) as e:
            last_validation_error = e
            logger.debug("Validation failed on attempt {}/{}: {}", validation_attempt + 1, validation_retries + 1, e)
            if validation_attempt < validation_retries:
                # Adjust temperature slightly to get different output
                temperature = min(1.0, temperature + 0.1)
                continue

    raise LLMValidationError(
        f"Failed to get valid structured response after {validation_retries + 1} attempts. "
        + f"Last validation error: {last_validation_error}"
    )
