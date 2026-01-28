"""
Used for manually testing the anthropic_oauth module.
"""

from typing import Annotated

import typer

from sculptor.services.config_service.anthropic_oauth import AnthropicAccountType, start_anthropic_oauth
from sculptor.services.config_service.api import ConfigService
from sculptor.services.config_service.data_types import AnthropicCredentials

app = typer.Typer()


class DemoAnthropicCredentialsService(ConfigService):
    def get_anthropic_credentials(self) -> AnthropicCredentials | None:
        return None

    def set_anthropic_credentials(self, anthropic_credentials: AnthropicCredentials):
        print(f"Anthropic credentials: {anthropic_credentials.model_dump_json()}")


@app.command()
def run(
    account_type: Annotated[
        AnthropicAccountType, typer.Option("--account-type")
    ] = AnthropicAccountType.ANTHROPIC_CONSOLE,
):
    server_thread, url = start_anthropic_oauth(
        # TODO: DemoAnthropicCredentialsService is abstract
        DemoAnthropicCredentialsService(),  # pyre-fixme[45]
        account_type,
    )
    print(url)
    server_thread.join()


if __name__ == "__main__":
    app()
