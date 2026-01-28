import threading
from typing import Final

from imbue_core.common import generate_id
from imbue_core.file_utils import atomic_writer_to
from sculptor.utils.build import get_sculptor_folder

INSTANCE_ID_FILE_NAME = "instance_id.txt"

_INSTANCE_ID_WRITING_LOCK: Final[threading.Lock] = threading.Lock()


def get_or_create_sculptor_instance_id() -> str:
    with _INSTANCE_ID_WRITING_LOCK:
        instance_id_file = get_sculptor_folder() / INSTANCE_ID_FILE_NAME
        if not instance_id_file.exists():
            with atomic_writer_to(instance_id_file) as instance_id_file_writer:
                instance_id_file_writer.write_text(generate_id())
        return instance_id_file.read_text()
