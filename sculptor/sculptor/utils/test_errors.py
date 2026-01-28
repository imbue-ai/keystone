from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Collection
from typing import Generator
from typing import Iterable
from typing import Mapping
from typing import TYPE_CHECKING

import boto3
import pytest
from _pytest.tmpdir import TempPathFactory
from moto import mock_s3

if TYPE_CHECKING:
    from mypy_boto3_s3 import Client  # type: ignore[missing-import]
else:
    Client = object

from imbue_core import s3_uploader
from imbue_core.itertools import only
from sculptor.utils.errors import ErrorAttachmentsS3Uploader
from sculptor.utils.errors import MAX_SENTRY_LIST_SIZE


@pytest.fixture(scope="function")
def mock_uploads_bucket() -> Generator[str, None, None]:
    bucket_name = s3_uploader.PRODUCTION_UPLOADS_BUCKET

    with mock_s3():
        # Explicitly set region to us-east-1 to make tests deterministic and avoid
        # region-related errors when AWS credentials are configured in the environment.
        # Without this, boto3 may pick up a region from environment variables or config
        # files, causing create_bucket() to fail with IllegalLocationConstraintException.
        test_client = boto3.client("s3", region_name="us-east-1")
        result = test_client.list_buckets()
        assert len(result["Buckets"]) == 0, "oops, are we not mocking the S3 correctly?"
        test_client.create_bucket(Bucket=bucket_name)
        result = test_client.list_buckets()
        assert len(result["Buckets"]) == 1

        # NOTE: this will re-initialize multiple times, but we don't care about it as long as the parameter is the same each time
        #       ideally the error uploading stop using a global object but that's for another time since there are more users
        #       of this global.
        s3_uploader.setup_s3_uploads(is_production=True)

        yield bucket_name


def _collect_and_upload(
    uploader: ErrorAttachmentsS3Uploader,
    *,
    exception: Exception | None,
    logs_folder: Path | None,
    database_path: Path | None,
) -> Mapping[str, Collection[str]]:
    uploads, callbacks = uploader.collect_external_attachments(
        exception=exception,
        logs_folder=logs_folder,
        database_path=database_path,
    )

    with ThreadPoolExecutor() as executor:
        results = executor.map(lambda c: c(), callbacks)
        # they all return None but may raise if something goes wrong
        for r in results:
            assert r is None

    return uploads


def _assert_uris_exist_in_bucket(bucket: str, s3_uris: Mapping[str, Iterable[str]]) -> None:
    # Use the same region as the fixture to ensure consistent behavior
    test_client: Client = boto3.client("s3", region_name="us-east-1")  # type: ignore[assignment]
    objects = test_client.list_objects(Bucket=bucket)

    # verify that all the returned keys were uploaded as expected
    uploaded_keys = set(o["Key"] for o in objects.get("Contents", []))
    expected_keys = set(u.removeprefix(f"s3://{bucket}/") for group in s3_uris.values() for u in group)
    assert uploaded_keys == expected_keys


def test_uploader_keys_match_uploads_and_are_grouped(
    mock_uploads_bucket: str, tmp_path_factory: TempPathFactory
) -> None:
    uploader = ErrorAttachmentsS3Uploader()

    try:
        raise Exception("fabricated test exception")
    except Exception as e:
        exc = e
    assert isinstance(exc, Exception)

    logs_folder = tmp_path_factory.mktemp("logs")
    database_path = tmp_path_factory.mktemp("database") / "database-mock.db"

    for file_to_populate in (
        database_path,
        logs_folder / "server" / "compressed-immutable-1.jsonl.gz",
        logs_folder / "server" / "compressed-immutable-2.jsonl.gz",
        logs_folder / "server" / "running.jsonl",
        logs_folder / "electron" / "electron.log",
    ):
        file_to_populate.parent.mkdir(parents=True, exist_ok=True)
        file_to_populate.write_text(f"fake contents of {file_to_populate.name}\n")

    uploads = _collect_and_upload(uploader, exception=exc, logs_folder=logs_folder, database_path=database_path)
    is_finished = uploader._wait_for_all_uploads(timeout=10)
    assert is_finished

    _assert_uris_exist_in_bucket(mock_uploads_bucket, uploads)

    assert set(uploads.keys()) == {"rotated_logs", "", "electron_logs", "live_logs"}, (
        "expecting all groups of logs to be covered by this test"
    )


def test_uploader_truncates_lists(mock_uploads_bucket: str, tmp_path_factory: TempPathFactory) -> None:
    uploader = ErrorAttachmentsS3Uploader()

    logs_folder = tmp_path_factory.mktemp("logs")
    (logs_folder / "server").mkdir(parents=True, exist_ok=True)
    for i in range(100):
        (logs_folder / "server" / f"{i}.jsonl").write_text(f"fake contents of {i}.jsonl\n")
        (logs_folder / "server" / f"{i}.jsonl.gz").write_text(f"fake contents of {i}.jsonl.gz\n")

    uploads = _collect_and_upload(uploader, exception=None, logs_folder=logs_folder, database_path=None)
    is_finished = uploader._wait_for_all_uploads(timeout=10)
    assert is_finished

    for group, uris in uploads.items():
        assert len(uris) <= MAX_SENTRY_LIST_SIZE, f"{group} exceeds the expected size"


def test_uploader_reuses_immutable_objects(mock_uploads_bucket: str, tmp_path_factory: TempPathFactory) -> None:
    uploader = ErrorAttachmentsS3Uploader()

    logs_folder = tmp_path_factory.mktemp("logs")
    server_logs_folder = logs_folder / "server"
    server_logs_folder.mkdir(parents=True, exist_ok=True)

    (server_logs_folder / "immutable.jsonl.gz").write_text("immutable")
    (server_logs_folder / "mutable.jsonl").write_text("mutable")
    uploads = _collect_and_upload(uploader, exception=None, logs_folder=logs_folder, database_path=None)
    is_finished = uploader._wait_for_all_uploads(timeout=10)
    assert is_finished
    _assert_uris_exist_in_bucket(mock_uploads_bucket, uploads)

    immutable_uri = only(uploads["rotated_logs"])

    with ThreadPoolExecutor() as executor:
        uploads_sets = executor.map(
            lambda _: _collect_and_upload(uploader, exception=None, logs_folder=logs_folder, database_path=None),
            range(10),
        )

    is_finished = uploader._wait_for_all_uploads(timeout=10)
    assert is_finished

    all_uploads_so_far = set()
    all_uploads_so_far.update(uploads)

    for uploads in uploads_sets:
        new_immutable_uri = only(uploads["rotated_logs"])
        assert new_immutable_uri == immutable_uri, "immutable logs should not get uploaded again"

        new_mutable_uri = only(u for g in uploads.values() for u in g if u != new_immutable_uri)
        assert new_mutable_uri not in all_uploads_so_far, "mutable keys should always get updated"
        all_uploads_so_far.add(new_mutable_uri)
