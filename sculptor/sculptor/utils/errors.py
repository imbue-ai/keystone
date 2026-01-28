import gzip
import re
import sys
import threading
from collections import defaultdict
from functools import cache
from functools import partial
from pathlib import Path
from sqlite3 import OperationalError
from typing import Callable
from typing import Collection
from typing import Iterable
from typing import Mapping
from typing import TypedDict
from typing import cast

import psutil
from loguru import logger
from pydantic import EmailStr
from pydantic import PrivateAttr
from sentry_sdk import get_current_scope
from sentry_sdk.types import Event
from sentry_sdk.types import Hint

from imbue_core.common import is_live_debugging
from imbue_core.error_utils import get_traceback_with_vars
from imbue_core.error_utils import setup_sentry
from imbue_core.pydantic_serialization import MutableModel
from imbue_core.s3_uploader import EXTRAS_UPLOADED_FILES_KEY
from imbue_core.s3_uploader import get_s3_upload_key
from imbue_core.s3_uploader import get_s3_upload_url
from imbue_core.s3_uploader import upload_to_s3_with_key
from imbue_core.s3_uploader import wait_for_s3_uploads
from imbue_core.sculptor.telemetry import mirror_exception_to_posthog
from imbue_core.thread_utils import ObservableThread
from sculptor.utils.build import BuildMetadata
from sculptor.utils.logs import COMPRESSED_LOG_EXTENSION
from sculptor.utils.logs import LOG_EXTENSION

# sentry's size limits are annoyingly hard to evaluate before sending the event. we'll just try to be conservative.
# https://docs.sentry.io/concepts/data-management/size-limits/
# https://develop.sentry.dev/sdk/data-model/envelopes/#size-limits
MAX_SENTRY_ATTACHMENT_SIZE = 10 * 1024 * 1024
# sentry truncates any lists attached to the event["extra"] to this number
# Maciek could not find the documentation for that behavior
MAX_SENTRY_LIST_SIZE = 10

_SENTRY_SCULPTOR_CONTEXT_KEY = "sculptor_config"


class SentrySculptorConfigDict(TypedDict):
    log_folder_path: Path | None
    db_path: Path | None


def _get_sculptor_config_from_scope() -> SentrySculptorConfigDict | None:
    scope = get_current_scope()._contexts.get(
        _SENTRY_SCULPTOR_CONTEXT_KEY, SentrySculptorConfigDict(db_path=None, log_folder_path=None)
    )
    # we only put SentrySculptorConfigDict in _contexts, but regrettably as a third-party library we can't tell pyre that
    return cast(SentrySculptorConfigDict, scope)


def _get_sculptor_log_folder_from_scope() -> Path | None:
    # TODO: _get_sculptor_config_from_scope() can be None. do we want to return None in that case, or error?
    log_folder_path = _get_sculptor_config_from_scope().get("log_folder_path")  # pyre-fixme[16]
    if log_folder_path and log_folder_path.exists():
        logger.debug("Using Sentry context log_folder_path: {}", str(log_folder_path))
        return log_folder_path
    logger.info("No log file path found")
    return None


def _get_sculptor_db_from_scope() -> Path | None:
    # TODO: _get_sculptor_config_from_scope() can be None. do we want to return None in that case, or error?
    db_path = _get_sculptor_config_from_scope().get("db_path")  # pyre-fixme[16]
    if db_path and db_path.exists():
        return db_path
    return None


def _get_disk_percentage_full() -> float | None:
    db_path = _get_sculptor_db_from_scope()
    if not db_path:
        return None
    return psutil.disk_usage(str(db_path)).percent


_CONTAINER_ENVIRONMENT_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("OrbStack", (".*orbstack.*",)),
    ("Colima", (".*colima.*",)),
    (
        "Rancher Desktop",
        (".*rancher-desktop.*", "^rd( +|$)"),
    ),
    ("Docker Desktop", (".*docker-desktop.*", ".*com.docker.backend.*", ".*com.docker.hyperkit.*")),
    ("Podman Desktop", (".*podman.*",)),
)


@cache
def _get_likely_container_environment() -> str | None:
    for process in psutil.process_iter(["name", "cmdline"]):
        for container_environment_name, patterns in _CONTAINER_ENVIRONMENT_PATTERNS:
            for pattern in patterns:
                if re.match(pattern, process.info["name"] or "") or any(
                    re.match(pattern, cmd) for cmd in (process.info["cmdline"] or [])
                ):
                    return container_environment_name
    return None


@cache
def _get_platform_info() -> str:
    return sys.platform


def _n_newest_files(files: Iterable[Path], n: int) -> Iterable[Path]:
    assert n > 0
    return sorted(files, key=lambda f: f.stat().st_mtime)[-n:]


class ErrorAttachmentsS3Uploader(MutableModel):
    # FIXME: use a local instance of s3_uploader instead of the global one?

    # stores all previously uploaded rotated logs
    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)
    _immutable_logs_keys: dict[Path, str] = PrivateAttr(default_factory=dict)

    @staticmethod
    def _upload_traceback_cb(key: str, exception: BaseException | None) -> None:
        tb_with_vars = get_traceback_with_vars(exception)
        if tb_with_vars is not None:
            upload_to_s3_with_key(key, tb_with_vars.encode())

    def _upload_file_cb(self, key: str, file_path: Path, compress: bool = False, immutable: bool = False) -> None:
        contents = file_path.read_bytes()
        if compress:
            # The highest compression level that still uses the fast pass implementation.
            # https://github.com/madler/zlib/blob/5a82f71ed1dfc0bec044d9702463dbdf84ea3b71/deflate.c#L117
            contents = gzip.compress(contents, compresslevel=3)
        uri = upload_to_s3_with_key(key, contents)
        if uri is not None:
            with self._lock:
                # we assume that uri and key are in sync
                self._immutable_logs_keys[file_path] = key

    def collect_external_attachments(
        self, *, exception: BaseException | None, logs_folder: Path | None, database_path: Path | None
    ) -> tuple[Mapping[str, Collection[str]], tuple[Callable, ...]]:
        """Prepares external uploads that will be attached to the error report.

        Returns external urls grouped by their logical names and the callbacks that need to be invoked which will
        actually perform the uploads to make those urls available.
        """
        uploads: dict[tuple[str, str], Callable | None] = {}

        if exception is not None:
            # NOTE: the following comment is copied without understanding
            # this traceback is from the logger call site!
            key = get_s3_upload_key("logsite_traceback_with_vars", ".txt")
            uploads[("", key)] = partial(self._upload_traceback_cb, key=key, exception=exception)

        if logs_folder:
            # upload all live log files
            for log_file in _n_newest_files(
                (logs_folder / "server").glob(f"*.{LOG_EXTENSION}"), n=MAX_SENTRY_LIST_SIZE
            ):
                key = get_s3_upload_key(log_file.stem, f".{LOG_EXTENSION}.{COMPRESSED_LOG_EXTENSION}")
                uploads[("live_logs", key)] = partial(self._upload_file_cb, key=key, file_path=log_file, compress=True)

            # upload each of the compressed log files
            for log_file in _n_newest_files(
                (logs_folder / "server").glob(f"*.{COMPRESSED_LOG_EXTENSION}"), n=MAX_SENTRY_LIST_SIZE
            ):
                with self._lock:
                    existing_key = self._immutable_logs_keys.get(log_file)

                if existing_key is not None:
                    logger.trace("Not uploading {} because it already exists under {}", log_file, existing_key)
                    uploads[("rotated_logs", existing_key)] = None
                else:
                    key = get_s3_upload_key(log_file.stem, f".{COMPRESSED_LOG_EXTENSION}")
                    uploads[("rotated_logs", key)] = partial(
                        self._upload_file_cb, key=key, file_path=log_file, immutable=True
                    )

            for log_file in _n_newest_files((logs_folder / "electron").glob("*.log"), n=MAX_SENTRY_LIST_SIZE):
                key = get_s3_upload_key(log_file.stem, f".log.{COMPRESSED_LOG_EXTENSION}")
                uploads[("electron_logs", key)] = partial(
                    self._upload_file_cb,
                    key=key,
                    file_path=log_file,
                    compress=True,
                )

        if database_path and database_path.exists():
            key = get_s3_upload_key("sculptor_db", ".db")
            uploads[("", key)] = partial(self._upload_file_cb, key=key, file_path=database_path)

        grouped_uris = defaultdict(list)
        for group, key in uploads.keys():
            grouped_uris[group].append(get_s3_upload_url(key))

        callbacks = tuple(c for c in uploads.values() if c is not None)
        return grouped_uris, callbacks

    @staticmethod
    def _wait_for_all_uploads(timeout: float | None) -> bool | None:
        """Only to be used for testing, to avoid coupling tests with the global object"""
        return wait_for_s3_uploads(timeout=timeout, is_shutting_down=False)


_ATTACHMENTS_UPLOADER = ErrorAttachmentsS3Uploader()


def add_extra_info_hook(event: Event, hint: Hint) -> tuple[Event, Hint, tuple[Callable, ...]]:
    """The add_extra_info_hook gets called in the SentryEventHandler. This seems a little too early in the process for
    sending things to s3.

    Sentry may still decide to discard the issue and in that scenario, executing all the uploads now would just
    blackhole them.
    """
    # Add live debugging state as a tag for easy filtering in Sentry UI
    if "tags" not in event:
        event["tags"] = {}
    event["tags"]["is_live_debugging"] = str(is_live_debugging())

    exception = sys.exception()
    if exception is None:
        try:
            raise Exception("this is an exception to get the current traceback")
        except Exception as e:
            exception = e

    s3_uri_groups, callbacks = _ATTACHMENTS_UPLOADER.collect_external_attachments(
        exception=exception,
        logs_folder=_get_sculptor_log_folder_from_scope(),
        database_path=_get_sculptor_db_from_scope(),
    )

    if s3_uri_groups:
        for group_name, s3_uris in s3_uri_groups.items():
            # NOTE: EXTRAS_UPLOADED_FILES_KEY is not safe to write to, as it may get stomped by other code paths
            extra_name = f"{EXTRAS_UPLOADED_FILES_KEY}_{group_name}"
            # NOTE: It is possible that there are pre-existing contents of this list that
            #       will bump the list size over the MAX_SENTRY_LIST_SIZE. Ignoring this edge
            #       as no one is expected to actually write to these at the moment of committing this.
            # TODO: perhaps move these to event["uploaded_files"][extra_name] so that
            # the type checker knows that the outcome has to be a list?
            event["extra"][extra_name] = event["extra"].get(extra_name, []) + s3_uris  # pyre-fixme[58]

    event["extra"]["disk_usage_percent"] = _get_disk_percentage_full()
    event["extra"]["platform"] = _get_platform_info()
    event["extra"]["container_environment"] = _get_likely_container_environment()
    return event, hint, tuple(callbacks)


def setup_sentry_with_context(
    build_metadata: BuildMetadata,
    log_folder: Path,
    db_path: Path | None,
    environment: str | None = None,
    global_user_context: Mapping[str, str] | None = None,
) -> None:
    # make sure all of our threads explode if we run into an irrecoverable exception
    ObservableThread.set_irrecoverable_exception_handler(is_irrecoverable_exception)

    setup_sentry(
        dsn=build_metadata.sentry_dsn,
        release_id=build_metadata.version,
        global_user_context=global_user_context,
        add_extra_info_hook=add_extra_info_hook,
        environment=environment,
        before_send=mirror_exception_to_posthog,
    )
    # Store the log file path in Sentry's global context
    scope = get_current_scope()
    scope.set_context(
        _SENTRY_SCULPTOR_CONTEXT_KEY,
        # need to cast to `dict` to make PyCharm happy
        cast(
            dict,
            SentrySculptorConfigDict(
                log_folder_path=log_folder,
                db_path=db_path,
            ),
        ),
    )
    scope.set_tag("git_sha", build_metadata.git_commit_sha)
    logger.info("Sentry initialized with DSN: {}", build_metadata.sentry_dsn)
    logger.info("Sentry initialized with log folder: {}", log_folder)


def set_sentry_user_for_current_scope(*, user_id: str, user_email: EmailStr) -> None:
    scope = get_current_scope()
    scope.set_user(
        {
            "username": str(user_email),  # for compatibility with older error reports that did not include email or id
            "id": user_id,
            "email": str(user_email),
        }
    )


def is_irrecoverable_exception(exception: BaseException) -> bool:
    """
    For some exceptions, we want to crash the app immediately.

    By convention, in these cases we also:
        - don't want to send the exception to Sentry because we can't really act on it
        - but we want to emit a posthog event to keep an eye on how often this happens

    """
    exception_message = str(exception)
    if isinstance(exception, OperationalError) and (
        "disk I/O error" in exception_message or "unable to open database file" in exception_message
    ):
        return True
    # Add more such cases here if needed.
    return False
