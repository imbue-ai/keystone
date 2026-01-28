import shutil
import stat
import threading
from pathlib import Path
from typing import Final

from imbue_core.file_utils import atomic_multifile_writer_into
from imbue_core.processes.local_process import run_blocking
from sculptor.utils.build import get_sculptor_folder
from sculptor.utils.file_utils import copy_dir


def copy_ssh_config(dest: Path):
    # TODO: Ensure atomic move to avoid potentially using half-copied config.
    #       Since we're copying these files using filesystem-based primitives, there's a chance
    #       we might be able to refer to these files directly instead of copying them.

    # Location of this file (ssh_utils.py)
    here = Path(__file__).resolve().parent

    # Source directory: adjacent ssh_config
    src = here / "ssh_config"

    # Make sure destination exists
    dest.mkdir(parents=True, exist_ok=True)

    # Copy everything (overwrite if exists)
    for item in src.iterdir():
        target = dest / item.name
        if item.is_dir():
            # Replace dir if already exists
            if target.exists():
                shutil.rmtree(target)
            copy_dir(item, target)
        else:
            shutil.copy2(item, target)
            if not target.is_symlink():
                stat_info = target.lstat()
                target.chmod(stat_info.st_mode | stat.S_IWUSR)


_SSH_CONFIGURATION_LOCK: Final[threading.Lock] = threading.Lock()


def ensure_local_sculptor_ssh_configured() -> Path:
    dot_sculptor = get_sculptor_folder()
    copy_ssh_config(dot_sculptor / "ssh")

    keypair_directory = dot_sculptor / "task_container_keypair"
    keypair_directory.mkdir(parents=True, exist_ok=True)

    dest_private = keypair_directory / "id_rsa"
    dest_public = keypair_directory / "id_rsa.pub"

    with _SSH_CONFIGURATION_LOCK:
        private_exists = dest_private.exists()
        public_exists = dest_public.exists()

        if private_exists and public_exists:
            return keypair_directory

        with atomic_multifile_writer_into(keypair_directory) as keypair_directory_writer:
            run_blocking(["ssh-keygen", "-t", "rsa", "-f", str(keypair_directory_writer / "id_rsa"), "-N", ""])

    return keypair_directory
