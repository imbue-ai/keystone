import os
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from sculptor.services.environment_service.providers.docker.image_fetch import download_image_tarball_if_needed


def set_mtime_to_2_days_ago(path: Path):
    two_days_ago = (datetime.now() - timedelta(days=2)).timestamp()
    os.utime(path, (path.stat().st_atime, two_days_ago))


def test_download_image_tarball_if_needed(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    # Create a fake tarball that will be "downloaded"
    test_tarball_content = b"fake_tarball_data"
    test_sha = "abc123def456"
    source_tarball = tmp_path / "test-image.tar"
    source_tarball.write_bytes(test_tarball_content)
    set_mtime_to_2_days_ago(source_tarball)

    with (
        patch(
            "sculptor.services.environment_service.providers.docker.image_fetch._CDN_PREFIX",
            f"file://{tmp_path}/",
        ),
        patch(
            "sculptor.services.environment_service.providers.docker.image_fetch.docker_image_url_to_s3_safe_name",
            return_value="test-image",
        ),
    ):
        image_url = f"test://test-image@sha256:{test_sha}"

        # First call should download the tarball
        result_path = download_image_tarball_if_needed(image_url, cache_dir)

        assert result_path.exists()
        assert result_path.read_bytes() == test_tarball_content

        # Second call should use cached version (no download)
        result_path_2 = download_image_tarball_if_needed(image_url, cache_dir)
        assert result_path_2 == result_path
        assert result_path_2.exists()


def test_cleanup_stale_image_tarballs_removes_partial_downloads(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    current_sha = "aabbccdd11223344"

    partial_files = {
        "0011aabb.tar.part.uuid1": b"partial1",
        "2233ccdd.tar.part.uuid2": b"partial2",
        "aabbccdd11223344.tar.part.uuid3": b"partial3",
    }

    # Create some partial download files from failed downloads
    for filename, content in partial_files.items():
        (cache_dir / filename).write_bytes(content)
        set_mtime_to_2_days_ago(cache_dir / filename)

    # Create source tarball for downloading
    current_source = tmp_path / "current-image.tar"
    current_source.write_bytes(b"current")
    set_mtime_to_2_days_ago(current_source)

    with (
        patch(
            "sculptor.services.environment_service.providers.docker.image_fetch._CDN_PREFIX",
            f"file://{tmp_path}/",
        ),
        patch(
            "sculptor.services.environment_service.providers.docker.image_fetch.docker_image_url_to_s3_safe_name",
            return_value="current-image",
        ),
    ):
        # Download the current version, which should trigger cleanup of partial files
        image_url = f"test://current-image@sha256:{current_sha}"
        result_path = download_image_tarball_if_needed(image_url, cache_dir)

        assert result_path.exists()

        # All partial files should be removed
        for filename in partial_files.keys():
            assert not (cache_dir / filename).exists()

        # Current tarball should remain
        assert result_path.read_bytes() == b"current"


def test_cleanup_stale_image_tarballs_removes_old_versions(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    # Create old tarballs from previous versions
    old_sha1 = "1234567890abcdef"
    old_sha2 = "fedcba0987654321"
    current_sha = "aabbccdd11223344"

    sha1_path = cache_dir / f"{old_sha1}.tar"
    sha1_path.write_bytes(b"old_version1")
    set_mtime_to_2_days_ago(sha1_path)

    sha2_path = cache_dir / f"{old_sha2}.tar"
    sha2_path.write_bytes(b"old_version2")
    set_mtime_to_2_days_ago(sha2_path)

    # Create source tarballs for downloading
    old_source1 = tmp_path / "old-image1.tar"
    old_source1.write_bytes(b"old_version1")
    set_mtime_to_2_days_ago(old_source1)
    old_source2 = tmp_path / "old-image2.tar"
    old_source2.write_bytes(b"old_version2")
    set_mtime_to_2_days_ago(old_source2)
    current_source = tmp_path / "current-image.tar"
    current_source.write_bytes(b"current")
    set_mtime_to_2_days_ago(current_source)

    with (
        patch(
            "sculptor.services.environment_service.providers.docker.image_fetch._CDN_PREFIX",
            f"file://{tmp_path}/",
        ),
        patch(
            "sculptor.services.environment_service.providers.docker.image_fetch.docker_image_url_to_s3_safe_name",
            return_value="current-image",
        ),
    ):
        # Download the current version, which should trigger cleanup
        image_url = f"test://current-image@sha256:{current_sha}"
        result_path = download_image_tarball_if_needed(image_url, cache_dir)

        assert result_path.exists()

        # Old tarballs should be removed
        assert not (cache_dir / f"{old_sha1}.tar").exists()
        assert not (cache_dir / f"{old_sha2}.tar").exists()

        # Current tarball should remain
        assert (cache_dir / f"{current_sha}.tar").exists()


def test_download_cleans_up_stale_files_after_success(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    test_tarball_content = b"fake_tarball_data"
    test_sha = "abc123def456"
    source_tarball = tmp_path / "test-image.tar"
    source_tarball.write_bytes(test_tarball_content)
    set_mtime_to_2_days_ago(source_tarball)

    # Create stale files that should be cleaned up
    (cache_dir / "0011223344556677.tar").write_bytes(b"old")
    set_mtime_to_2_days_ago((cache_dir / "0011223344556677.tar"))
    (cache_dir / "abc123def456.tar.part.old-uuid").write_bytes(b"partial")
    set_mtime_to_2_days_ago((cache_dir / "abc123def456.tar.part.old-uuid"))

    with (
        patch(
            "sculptor.services.environment_service.providers.docker.image_fetch._CDN_PREFIX",
            f"file://{tmp_path}/",
        ),
        patch(
            "sculptor.services.environment_service.providers.docker.image_fetch.docker_image_url_to_s3_safe_name",
            return_value="test-image",
        ),
    ):
        image_url = f"test://test-image@sha256:{test_sha}"

        result_path = download_image_tarball_if_needed(image_url, cache_dir)

        assert result_path.exists()
        assert result_path.read_bytes() == test_tarball_content

        # Stale files should be cleaned up
        assert not (cache_dir / "0011223344556677.tar").exists()
        assert not (cache_dir / "abc123def456.tar.part.old-uuid").exists()
