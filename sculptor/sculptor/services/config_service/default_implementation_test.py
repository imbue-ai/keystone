import tempfile
from pathlib import Path

from imbue_core.secrets_utils import Secret
from sculptor.services.config_service.data_types import AnthropicApiKey
from sculptor.services.config_service.data_types import Credentials
from sculptor.services.config_service.utils import populate_credentials_file as populate_credentials_file


def test_serialize_and_deserialize_secret() -> None:
    original_key = AnthropicApiKey(anthropic_api_key=Secret("sk-ant-ort01-"), generated_from_oauth=True)
    with tempfile.TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        credentials_file_path = temp_dir.joinpath("credentials.json")

        populate_credentials_file(path=credentials_file_path, credentials=Credentials(anthropic=original_key))
        credentials_content = credentials_file_path.read_text()
        print(credentials_content)
        new_credentials = Credentials.model_validate_json(credentials_content)
        assert new_credentials.anthropic == original_key
