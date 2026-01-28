import anthropic
import openai
from anthropic.types import TextBlock
from anthropic.types import TextBlockParam

DEFAULT_ANTHROPIC_MODEL = "claude-3-7-sonnet-20250219"
DEFAULT_OPENAI_MODEL = "gpt-4"


class LLMClient:
    def get_response(
        self,
        max_tokens: int,
        temperature: float,
        messages: list,
        system: list[TextBlockParam],
        model: str | None = None,
    ) -> str: ...


class AnthropicClient(LLMClient):
    client: anthropic.Anthropic

    def __init__(self, client: anthropic.Anthropic) -> None:
        self.client = client

    def get_response(
        self,
        max_tokens: int,
        temperature: float,
        messages: list,
        system: list[TextBlockParam],
        model: str | None = None,
    ) -> str:
        if model is None:
            model = DEFAULT_ANTHROPIC_MODEL
        response = self.client.messages.create(
            model=model, max_tokens=max_tokens, temperature=temperature, messages=messages, system=system
        )

        if response.content and len(response.content) > 0:
            content_block = response.content[0]
            match content_block:
                case TextBlock(text=text):
                    return text
                case _:
                    raise LLMAPIError(f"Unexpected content type in response: {type(content_block)}")
        else:
            raise LLMAPIError("Empty response from LLM")


class OpenAIClient(LLMClient):
    client: openai.OpenAI

    def __init__(self, client: openai.OpenAI) -> None:
        self.client = client

    def get_response(
        self,
        max_tokens: int,
        temperature: float,
        messages: list,
        # bleh, this is kinda messy
        system: list[TextBlockParam],
        model: str | None = None,
    ) -> str:
        if model is None:
            model = DEFAULT_OPENAI_MODEL
        system_text = "\n\n".join([block["text"] for block in system if block.get("text") is not None])
        messages = [
            {"role": "system", "content": system_text},
            *messages,
        ]

        response = self.client.chat.completions.create(
            model=model,
            max_completion_tokens=max_tokens,
            # For some reason, OpenAI only allows the default temperature of 1
            # temperature=temperature,
            messages=messages,
        )
        if response.choices and len(response.choices) > 0:
            return response.choices[0].message.content
        raise LLMAPIError("Empty response from LLM")


class LLMError(Exception):
    """Base exception for LLM utilities."""


class LLMAPIError(LLMError):
    """Exception raised when the LLM API call fails."""


class LLMValidationError(LLMError):
    """Exception raised when structured output validation fails after retries."""
