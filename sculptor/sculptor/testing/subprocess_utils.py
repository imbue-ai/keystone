import os
import subprocess
import threading
from typing import Callable

from loguru import logger


class Forwarder(threading.Thread):
    """Thread to forward output from the sculptor server to the logger.

    While the sculptor server is running, there might be useful output that we want to log.
    """

    def __init__(
        self,
        sculptor_server: subprocess.Popen,
        prefix: str = "",
        known_harmless_func: Callable[[str], bool] | None = None,
    ) -> None:
        super().__init__(daemon=True)
        self.prefix = prefix
        self.sculptor_server = sculptor_server
        self.first_failure_line = None
        self.known_harmless_func = known_harmless_func

    def run(self) -> None:
        assert self.sculptor_server.stdout, "Sculptor server stdout is always available in PIPE mode"
        for line in self.sculptor_server.stdout:
            # Note: the print(line) here routes to pytest junit due to an issue with how pytest hides stdout
            #       the logger actually displays to the user
            print_colored_line(self.prefix + line.rstrip(), known_harmless_func=self.known_harmless_func)
            if "|ERROR" in line or "Cache miss" in line:
                # note that we do NOT blow up here -- that's because we want to capture all the output!
                self.first_failure_line = line.rstrip()
                # raise RuntimeError(line.strip())


def print_colored_line(
    line: str, level: str | None = None, known_harmless_func: Callable[[str], bool] | None = None
) -> None:
    # FIXME: make this the only case ASAP -- the else is just there because these lines end up being too long for the old dumb runner...
    if os.environ.get("IMBUE_MODAL_TEST"):
        if known_harmless_func is not None and known_harmless_func(line):
            logger.info("Known harmless: {}", line)
        elif "|ERROR" in line or level == "ERROR":
            logger.error(line)
        elif "|WARNING" in line or level == "WARNING":
            logger.warning(line)
        elif "|INFO" in line or level == "INFO":
            logger.info(line)
        elif "|DEBUG" in line or level == "DEBUG":
            logger.debug(line)
        else:
            logger.info(line)
    else:
        if known_harmless_func is not None and known_harmless_func(line):
            print(f"\033[32mKnown harmless: {line}\033[0m")
        elif "|ERROR" in line or level == "ERROR":
            # Red
            print(f"\033[31m{line}\033[0m")
        elif "|WARNING" in line or level == "WARNING":
            # Yellow
            print(f"\033[33m{line}\033[0m")
        elif "|INFO" in line or level == "INFO":
            # Green
            print(f"\033[32m{line}\033[0m")
        elif "|DEBUG" in line or level == "DEBUG":
            # Cyan
            print(f"\033[36m{line}\033[0m")
        elif "|TRACE" in line or level == "TRACE":
            # Gray
            # print(f"\033[90m{line}\033[0m")
            pass
        else:
            print(line)
