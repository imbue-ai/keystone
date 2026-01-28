from imbue_core.pydantic_serialization import SerializableModel


class SlashCommand(SerializableModel):
    """
    See e.g. https://code.claude.com/docs/en/slash-commands for more details.

    (This concept seems to be common across multiple agents/platforms.)

    """

    # The actual value of the slash command (e.g. "/foo:bar").
    value: str
    # How is the slash command displayed in the UI dropdown (e.g. "foo:bar (user)").
    display_name: str
