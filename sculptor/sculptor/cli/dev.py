"""
# Publish images to CloudFront CDN via S3:
```sh
uv run sculptor/sculptor/cli/dev.py publish-control-plane-and-default-dev-container-to-s3
```
"""

import json
import mimetypes
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Final

import boto3
import typer
from loguru import logger

from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.git import get_git_repo_root
from imbue_core.itertools import only
from imbue_core.processes.local_process import run_blocking
from imbue_core.thread_utils import ObservableThread
from sculptor.cli.changelog.enrichment import enrich_merge_commits
from sculptor.cli.changelog.git_utils import get_commit_timestamp
from sculptor.cli.changelog.git_utils import get_merge_commits
from sculptor.cli.changelog.git_utils import get_versions
from sculptor.cli.changelog.markdown import generate_markdown_changelog
from sculptor.cli.changelog.notion import create_notion_changelog
from sculptor.cli.changelog.notion import find_existing_notion_page
from sculptor.cli.changelog.notion import get_notion_token
from sculptor.cli.dev_commands.make_default_docker_images import make_default_images
from sculptor.primitives.constants import CONTROL_PLANE_LOCAL_TAG_PATH
from sculptor.primitives.constants import CONTROL_PLANE_MANIFEST_PATH
from sculptor.primitives.constants import CONTROL_PLANE_TAG_PATH
from sculptor.primitives.executor import ObservableThreadPoolExecutor
from sculptor.services.environment_service.providers.docker.devcontainer_image_builder import (
    docker_pull_default_devcontainer,
)
from sculptor.services.environment_service.providers.docker.devcontainer_image_builder import (
    get_default_devcontainer_image_reference,
)
from sculptor.services.environment_service.providers.docker.image_fetch import docker_image_url_to_s3_safe_name
from sculptor.services.environment_service.providers.docker.volume_mounted_nix_control_plane import (
    ControlPlaneImageNameProvider,
)
from sculptor.services.environment_service.providers.docker.volume_mounted_nix_control_plane import ControlPlaneRunMode

AUTOMATED_CHANGELOG_NOTION_DATABASE_ID: Final[str] = "293a550faf95808a9d44e137614e4b86"

CONTROL_PLANE_REQUIRED_SUBPROJECTS = (
    "imbue",
    "imbue_core",
    "imbue_cli",
    "imbue_tools",
    "imbue_verify",
    "imbue_retrieve",
)

typer_cli = typer.Typer(
    name="sculptor_dev",
    help="A set of tools for developing Sculptor itself.",
    no_args_is_help=True,
    invoke_without_command=False,
    pretty_exceptions_enable=False,
)


@typer_cli.command(help="Used to build our default Docker images during integration testing.")
def make_default_docker_images() -> None:
    make_default_images()


@typer_cli.command(help="Ensure that we have all of the docker data we need.")
def load_docker_data() -> None:
    # first try to load the images if they exist
    run_blocking(["docker", "load", "-i", "/tmp/control_plane.tar"])
    run_blocking(["docker", "load", "-i", "/tmp/default_devcontainer.tar"])
    # sigh, it's lame, but we need to docker pull here, otherwise docker doesn't realize that these layers exist
    # and then our later logic doesn't work out

    run_blocking(["docker", "pull", get_default_devcontainer_image_reference()])
    # the below will be done only in the tests where this is necessary
    # # then make sure that everything is registered properly
    # threads = start_control_plane_background_setup(thread_suffix="FetchDockerData")
    # for thread in threads:
    #     thread.join()


@typer_cli.command(
    help="Ensure that we have all of the docker data we need and then save it out for more convenient use in modal."
)
def fetch_docker_data() -> None:
    # go grab the images
    # doing this explicitly instead, not this old way
    # threads = start_control_plane_background_setup(thread_suffix="FetchDockerData")
    # for thread in threads:
    #     thread.join()
    # we explicitly just download, no need to make the volume
    with ConcurrencyGroup(name="fetching_docker_images") as concurrency_group:
        # annoyingly, the default devcontainer download is broken (it ends up pulling from ghcr instead)
        control_plane_local_build_thread = ObservableThread(
            target=build_control_plane_locally, kwargs={"use_depot": True}
        )
        default_devcontainer_thread = ObservableThread(
            target=docker_pull_default_devcontainer, args=(concurrency_group,)
        )
        control_plane_local_build_thread.start()
        default_devcontainer_thread.start()
        control_plane_local_build_thread.join()
        default_devcontainer_thread.join()

    local_image_and_tag = ControlPlaneImageNameProvider(
        predetermined_run_mode=ControlPlaneRunMode.LOCALLY_BUILT
    ).determine_control_plane_image_name()
    run_blocking(["docker", "save", "-o", "/tmp/control_plane.tar", local_image_and_tag])

    # then docker save them out to /tmp
    run_blocking(["docker", "save", "-o", "/tmp/default_devcontainer.tar", get_default_devcontainer_image_reference()])


def _save_and_upload_image(image_url: str, image_type: str, platform: str, client) -> None:
    """Save a Docker image and upload it to S3 for a specific platform."""
    logger.info(f"Processing {image_type} image for {platform} platform: {image_url}")

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_file_path = Path(temp_dir) / "docker_save.tar"

        # Pull the image for the specified platform
        pull_cmd = f"docker pull --platform linux/{platform} {image_url}"
        logger.info("Running: {}", pull_cmd)
        pull_result = os.system(pull_cmd)
        if pull_result != 0:
            raise RuntimeError(f"Failed to pull {image_url} for platform {platform}")

        # Save the image to tar file
        save_cmd = f"docker save {image_url} -o {str(temp_file_path)}"
        logger.info("Running: {}", save_cmd)
        save_result = os.system(save_cmd)
        if save_result != 0:
            raise RuntimeError(f"Failed to save {image_url}")

        # Upload to S3 with safe name that includes image URL and platform
        safe_name = docker_image_url_to_s3_safe_name(image_url, platform)
        s3_path = f"s3://imbue-sculptor-latest/images/{safe_name}.tar"
        logger.info("Uploading to: {}", s3_path)
        _upload_file(temp_file_path, f"images/{safe_name}.tar", "imbue-sculptor-latest", client)
        logger.success("Successfully uploaded image to S3: {}", s3_path)


# TODO: Move this into imbue_core, as test_shotgun/common.py also has this
def _upload_file(local_path: Path, s3_key: str, bucket: str, client):
    # Automatically determine content type from file extension
    content_type, _ = mimetypes.guess_type(str(local_path))
    extra_args = {}
    if content_type:
        extra_args["ContentType"] = content_type

    client.upload_file(str(local_path), bucket, s3_key, ExtraArgs=extra_args)


def _build_control_plane(
    use_depot: bool,
    commit_hash: str,
    image_tag: str,
    push_image: bool,
) -> None:
    """Build the control plane image and upload it to S3.

    Useful:
    * `brew install depot/tap/depot`, see: https://depot.dev/docs/cli/installation
    """
    assert Path(".git").exists(), "This command must be run from the git repo root"
    project_files_dir = Path("sculptor/claude-container/build/project-files")
    project_files_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("pyproject.toml", "uv.lock"):
        shutil.copy(filename, str(project_files_dir / filename))
    sub_projects = CONTROL_PLANE_REQUIRED_SUBPROJECTS
    for sub_project in sub_projects:
        sub_project_dir = project_files_dir / sub_project
        sub_project_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(str(Path(sub_project) / "pyproject.toml"), str(sub_project_dir / "pyproject.toml"))

    run_blocking(
        [
            "git",
            "archive",
            "--format=zip",
            "-o",
            "sculptor/claude-container/build/control-plane-src.zip",
            "HEAD",
            *sub_projects,
        ]
    )
    # Touch all files to a fixed time so that we can cache them if they didn't change.
    run_blocking(["bash", "-c", "find sculptor/claude-container/build/ -exec touch -t 202411141230.00 {} +"])
    git_branch = run_blocking(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    user_name = run_blocking(["id", "-un"]).stdout.strip()
    build_time = run_blocking(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"]).stdout.strip()
    last_commit_date = run_blocking(["git", "log", "-1", "--date=format:%Y%m%d", "--format=%cd"]).stdout.strip()
    last_commit_time = run_blocking(["git", "log", "-1", "--format=%cI"]).stdout.strip()
    hostname = run_blocking(["hostname"]).stdout.strip()

    builder = ["depot"] if use_depot else ["docker", "buildx"]

    platform = ("--platform", "linux/amd64,linux/arm64")
    if not push_image:
        # local build, so no need for multi-platform; just use the default
        platform = ()

    build_args = [
        *builder,
        "build",
        *("-f", "sculptor/claude-container/Dockerfile.base_nix"),
        *("--build-arg", f"_IMBUE_BUILT_FROM_GIT_HASH={commit_hash}"),
        *("--build-arg", f"_IMBUE_BUILT_FROM_GIT_BRANCH={git_branch}"),
        *("--build-arg", f"_IMBUE_BUILT_BY_USER={user_name}"),
        *("--build-arg", f"_IMBUE_BUILT_AT_TIMESTAMP={build_time}"),
        *("--build-arg", f"_IMBUE_BUILT_AT_LAST_COMMIT_DATE={last_commit_date}"),
        *("--build-arg", f"_IMBUE_BUILT_AT_LAST_COMMIT_TIMESTAMP={last_commit_time}"),
        *("--build-arg", f"_IMBUE_BUILT_ON_HOSTNAME={hostname}"),
        *platform,
        "sculptor/claude-container/",
    ]
    if push_image:
        if use_depot:
            build_args.extend(["-t", image_tag, "--push", "--save"])
        else:
            build_args.extend(
                [
                    "-t",
                    image_tag,
                    "--push",
                    *("--build-arg", "BUILDKIT_INLINE_CACHE=1"),
                    "--cache-to=type=registry,ref=ghcr.io/imbue-ai/scuptorbase_nix_buildcache:buildcache,mode=max",
                    "--cache-from=type=registry,ref=ghcr.io/imbue-ai/scuptorbase_nix_buildcache:buildcache",
                ]
            )
    else:
        build_args.extend(["-t", image_tag, "--load"])

    process = subprocess.Popen(
        build_args,
        env=None if use_depot else {**os.environ, "DOCKER_BUILDKIT": "1"},
        stdin=subprocess.DEVNULL,
        stderr=sys.stderr,
        stdout=sys.stdout,
    )
    exit_code = process.wait()
    assert exit_code == 0, "Docker build failed"


def _publish_control_plane_to_s3(image_ghcr_tag_url: str, manifest_obj: dict) -> None:
    client = boto3.client("s3")
    manifests = manifest_obj["manifests"]
    platforms = ["arm64", "amd64"]
    futures = []
    with ConcurrencyGroup(name="control_plane_building") as cg:
        with ObservableThreadPoolExecutor(cg, max_workers=2, thread_name_prefix="DockerDownloader") as executor:
            for platform in platforms:
                manifest = only(x for x in manifests if x["platform"]["architecture"] == platform)
                digest = manifest["digest"]
                control_plane_image_url = f"{image_ghcr_tag_url}@{digest}"
                logger.info(f"Starting upload of Docker control plane images to S3: {control_plane_image_url}")
                f = executor.submit(_save_and_upload_image, control_plane_image_url, "control_plane", platform, client)
                futures.append(f)
    # raise any exceptions
    for f in futures:
        f.result()

    logger.success("Successfully published control plane image to S3!")


@typer_cli.command("build-control-plane", help="Build/publish/upload the control plane")
def build_control_plane(use_depot: bool = True, debug: bool = False, update_pinned_version: bool = True) -> None:
    # when we're not debugging, we must be committed
    suffix = ""
    is_clean_result = run_blocking(["git", "status", "--porcelain"])
    if is_clean_result.stdout != "" or is_clean_result.stderr != "" or is_clean_result.returncode != 0:
        if debug:
            suffix = "-dirty"
        else:
            raise RuntimeError(
                f"Git working directory is not clean. Please commit or stash changes first. git status --porcelain result was : \n\n{is_clean_result}"
            )

    commit_hash = run_blocking(["git", "rev-parse", "HEAD"]).stdout.strip() + suffix
    image_ghcr_tag_url = f"ghcr.io/imbue-ai/sculptorbase_nix:{commit_hash}"

    _build_control_plane(
        use_depot=use_depot,
        commit_hash=commit_hash,
        image_tag=image_ghcr_tag_url,
        push_image=True,
    )

    manifest_obj = json.loads(run_blocking(["docker", "manifest", "inspect", image_ghcr_tag_url]).stdout)
    _publish_control_plane_to_s3(image_ghcr_tag_url, manifest_obj)

    if update_pinned_version:
        # TODO(gbrova): we will soon stop pinning the version to a file; when that happens, remove this code and flag
        (get_git_repo_root() / CONTROL_PLANE_TAG_PATH).write_text(commit_hash + "\n")
        (get_git_repo_root() / CONTROL_PLANE_MANIFEST_PATH).write_text(json.dumps(manifest_obj) + "\n")


@typer_cli.command(help="Build the control plane without publishing it")
def build_control_plane_locally(
    use_depot: bool = False,
    skip_clean_check: bool = False,
) -> None:
    if not skip_clean_check:
        is_clean_result = run_blocking(["git", "status", "--porcelain"])
        if is_clean_result.stdout != "" or is_clean_result.stderr != "" or is_clean_result.returncode != 0:
            raise RuntimeError(
                f"Git working directory is not clean. Please commit or stash changes first. git status --porcelain result was : \n\n{is_clean_result}"
            )

    commit_hash = run_blocking(["git", "rev-parse", "HEAD"]).stdout.strip()
    (get_git_repo_root() / CONTROL_PLANE_LOCAL_TAG_PATH).write_text(commit_hash + "\n")

    local_image_and_tag = ControlPlaneImageNameProvider(
        predetermined_run_mode=ControlPlaneRunMode.LOCALLY_BUILT
    ).determine_control_plane_image_name()

    _build_control_plane(
        use_depot=use_depot,
        commit_hash=commit_hash,
        image_tag=local_image_and_tag,
        push_image=False,
    )


@typer_cli.command(help="Publish control plane and default dev container to S3 for both arm64 and amd64 platforms.")
def publish_control_plane_and_default_dev_container_to_s3() -> None:
    """
    Publish both control plane and default dev container images to S3.
    Creates 4 files total: [control_plane, default_devcontainer] x [arm64, amd64]
    """
    control_plane_image = ControlPlaneImageNameProvider(
        predetermined_run_mode=ControlPlaneRunMode.TAGGED_RELEASE
    ).determine_control_plane_image_name()
    default_devcontainer_image = get_default_devcontainer_image_reference()
    client = boto3.client("s3")

    platforms = ["arm64", "amd64"]
    images_to_process = [
        (control_plane_image, "control_plane"),
        (default_devcontainer_image, "default_devcontainer"),
    ]

    logger.info("Starting upload of Docker images to S3...")
    logger.info(f"Control plane image: {control_plane_image}")
    logger.info(f"Default devcontainer image: {default_devcontainer_image}")

    # Process each combination of image and platform
    for image_url, image_type in images_to_process:
        for platform in platforms:
            _save_and_upload_image(image_url, image_type, platform, client)

    logger.success("Successfully published all images to S3!")


@typer_cli.command(help="Generate enriched changelog data between two version tags")
def generate_changelog(
    from_version: str = typer.Argument(..., help="Starting version (e.g., '0.2.4')"),
    to_version: str = typer.Argument("HEAD", help="Ending version (defaults to HEAD)"),
    output_file: str | None = typer.Option(None, "--output", "-o", help="Output file path (defaults to stdout)"),
    format: str = typer.Option("jsonl", "--format", "-f", help="Output format: 'jsonl' or 'markdown'"),
    template: str | None = typer.Option(
        None, "--template", "-t", help="Path to custom Jinja2 template (markdown only)"
    ),
    notion_database_id: str | None = typer.Option(
        AUTOMATED_CHANGELOG_NOTION_DATABASE_ID,
        "--notion-database-id",
        "-n",
        help="Notion database ID to create entries in",
    ),
) -> None:
    """Generate enriched changelog data between two versions."""
    logger.info(f"Generating changelog from {from_version} to {to_version}")

    commits = get_merge_commits(from_version, to_version)
    if not commits:
        logger.error("No commits found")
        raise typer.Exit(1)

    enriched_commits = enrich_merge_commits(commits)
    cut_time = get_commit_timestamp(to_version)

    if format.lower() == "markdown":
        template_path = Path(template) if template else None
        markdown = generate_markdown_changelog(from_version, to_version, enriched_commits, cut_time, template_path)

        if output_file:
            Path(output_file).write_text(markdown)
            logger.success(f"Markdown changelog written to {output_file}")
        else:
            print(markdown)
        logger.success(f"Generated markdown changelog for {len(enriched_commits)} commits")
    elif format.lower() == "jsonl":
        changelog_data = [commit.model_dump() for commit in enriched_commits]

        if output_file:
            with open(output_file, "w") as f:
                for entry in changelog_data:
                    f.write(json.dumps(entry) + "\n")
            logger.success(f"JSONL changelog written to {output_file}")
        else:
            for entry in changelog_data:
                print(json.dumps(entry))
        logger.success(f"Generated JSONL changelog for {len(changelog_data)} commits")
    else:
        logger.error(f"Unknown format: {format}. Use 'jsonl' or 'markdown'")
        raise typer.Exit(1)

    if notion_database_id:
        create_notion_changelog(enriched_commits, notion_database_id, from_version, to_version, cut_time)


@typer_cli.command(help="Generate changelog for the most recent version if it doesn't exist in Notion")
def generate_most_recent_changelog(
    notion_database_id: str = typer.Option(
        AUTOMATED_CHANGELOG_NOTION_DATABASE_ID,
        "--notion-database-id",
        "-n",
        help="Notion database ID to create entries in",
    ),
) -> None:
    """
    Generate a changelog for the most recent version if it doesn't already exist in Notion.

    This command will:
    1. Find all versions from git history
    2. Take the last two versions
    3. Check if a changelog already exists in Notion for this version
    4. If not, generate and upload the changelog to Notion
    """
    versions = get_versions()
    if len(versions) < 2:
        logger.error(f"Need at least 2 versions, found {len(versions)}")
        raise typer.Exit(1)

    # Get the last two versions (most recent two)
    latest_version = versions[0]
    previous_version = versions[1]

    logger.info(f"Latest version: {latest_version}")
    logger.info(f"Previous version: {previous_version}")

    notion_token = get_notion_token()
    page_title = f"v{previous_version} to v{latest_version}"
    existing_page_id = find_existing_notion_page(notion_database_id, notion_token, page_title)

    if existing_page_id:
        logger.info(
            f"Changelog for {previous_version} to {latest_version} already exists in Notion (page ID: {existing_page_id})"
        )
        logger.info("Skipping changelog generation")
        return

    logger.info(f"No existing changelog found. Generating changelog from {previous_version} to {latest_version}...")

    commits = get_merge_commits(previous_version, latest_version)
    if not commits:
        logger.error("No commits found")
        raise typer.Exit(1)

    enriched_commits = enrich_merge_commits(commits)
    cut_time = get_commit_timestamp(latest_version)
    success = create_notion_changelog(
        enriched_commits,
        notion_database_id,
        previous_version,
        latest_version,
        cut_time,
    )

    if success:
        logger.success(f"Successfully generated changelog for {previous_version} to {latest_version}")
    else:
        logger.error("Failed to generate changelog")
        raise typer.Exit(1)


if __name__ == "__main__":
    typer_cli()
