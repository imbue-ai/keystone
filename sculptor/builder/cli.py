#!/usr/bin/env python3
"""This build script contains various functions used to assemble the build
artifact of Sculptor.

By only building the wheels we need, we save from having to import all of the
generally_intelligent repo.
"""

import base64
import fnmatch
import functools
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from datetime import timezone
from importlib import resources
from pathlib import Path
from typing import Container
from typing import Literal
from typing import assert_never

import tomlkit
import typer
from builder import darwin
from builder.artifacts import BuildStage
from builder.artifacts import PLATFORM_ARCH_TO_TARGET
from builder.artifacts import artifacts_for_target_and_stage

import imbue_core.git
from sculptor import sentry_settings
from sculptor.services.environment_service.providers.docker.devcontainer_image_builder import (
    get_default_devcontainer_image_reference,
)
from sculptor.services.environment_service.providers.docker.volume_mounted_nix_control_plane import (
    ControlPlaneImageNameProvider,
)
from sculptor.services.environment_service.providers.docker.volume_mounted_nix_control_plane import ControlPlaneRunMode
from sculptor.version import VersionComponent
from sculptor.version import dev_git_sha
from sculptor.version import dev_semver
from sculptor.version import is_prerelease
from sculptor.version import next_version
from sculptor.version import pep_440_to_semver

app = typer.Typer(pretty_exceptions_enable=False)


# These set convenient defaults on subprocess.run that text-decodes output and raises on non-zero exit status

_run_out = functools.partial(subprocess.run, check=True, stdout=sys.stdout, text=True)  # Writes to standard out
_run_pipe = functools.partial(
    subprocess.run, check=True, stdout=subprocess.PIPE, text=True
)  # Writes to a pipe for checking


@app.command("create-publication-artifacts")
def create_publication_artifacts() -> None:
    """Creates publication artifacts for Sculptor (dmg, deb, rpm) specific to
    the current platform.

    This command does the heavy lifting for building artifacts. After this, the artifacts will be available in
    `generally_intelligent/dist`
    """
    _run_out(["just", "refresh", "pkg"])


@app.command("setup-build-vars")
def setup_build_vars(environment: str) -> None:
    """Depending on the build environment, we will set up the build variables."""
    # match environment against the known environments, and export the following variables
    release_id: str
    frontend_dsn: str
    match environment:
        case "dev":
            release_id = dev_semver() + "-dev"
            frontend_dsn = sentry_settings.SCULPTOR_DEV_FRONTEND_SENTRY_DSN
        case "testing":
            release_id = dev_semver() + "-testing"
            frontend_dsn = sentry_settings.SCULPTOR_TESTING_FRONTEND_SENTRY_DSN
        case "production":
            release_id = dev_semver()
            frontend_dsn = sentry_settings.SCULPTOR_PRODUCTION_FRONTEND_SENTRY_DSN
        case str():
            typer.secho("Invalid environment specified. Must be one of: dev, testing, prod.", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        case _ as never:
            assert_never(never)

    typer.echo(f"export SCULPTOR_SENTRY_RELEASE_ID='{release_id}'")
    typer.echo(f"export SCULPTOR_FRONTEND_SENTRY_DSN='{frontend_dsn}'")


@app.command("cut-release")
def cut_release(
    dry_run: bool = typer.Option(
        False,  # default → real upload
        "--dry-run/--no-dry-run",
        "-n",  # short alias for --dry-run
        help="Pass --dry-run (-n) to skip uploading or --no-dry-run to force the actual upload.",
    ),
    bypass_checks: bool = typer.Option(False, "--bypass-checks", help="Bypass branch protection checks"),
    dist_dir: Path = typer.Option("../dist", help="Directory that holds build artefacts."),
) -> None:
    """Cut a new release branch from main and tag it."""
    if not bypass_checks:
        ensure_on_branch("main")
        ensure_clean_tree()

    target_release_version = dev_semver()
    release_candidate_version = next_version(target_release_version, VersionComponent.PRE_RELEASE)

    typer.echo(f"Begining a release branch for {target_release_version}.")

    # Verify there isn't a release tag and release branch for this.
    _run_out(["git", "fetch", "--tags"])
    _run_out(["git", "fetch"])

    if _run_pipe(["git", "tag", "--list", f"sculptor-v{release_candidate_version}"]).stdout:
        typer.echo("A release tag already exists for this version. Did you need to bump the version first?")
        raise typer.Exit(code=1)

    if _run_pipe(["git", "branch", "--list", f"release/{release_candidate_version}"]).stdout:
        typer.echo("A branch already exists for this version, but no release tag.")
        typer.echo("A prior release cut failed. Please delete the branch from origin and try again.")
        raise typer.Exit(code=1)

    # Write the rc version to the pyproject.toml file.
    commit_new_version(f"release/sculptor-v{target_release_version}", release_candidate_version, dry_run=dry_run)

    typer.echo(f"Created a new release branch for Sculptor {release_candidate_version} from git sha {dev_git_sha()}")

    if not dry_run:
        push_tags(release_candidate_version)
        typer.secho("Release complete.", fg=typer.colors.GREEN)
    else:
        typer.secho("Would have released, but dry-run mode was enabled", fg=typer.colors.YELLOW)


@app.command("fixup-release")
def fixup_release(
    dry_run: bool = typer.Option(
        False,  # default → real upload
        "--dry-run/--no-dry-run",
        "-n",  # short alias for --dry-run
        help="Pass --dry-run (-n) to skip uploading or --no-dry-run to force the actual upload.",
    ),
    bypass_checks: bool = typer.Option(False, "--bypass-checks", help="Bypass branch protection checks"),
    dist_dir: Path = typer.Option("../dist", help="Directory that holds build artefacts."),
) -> None:
    """Cut a new release branch from main and tag it."""
    if not bypass_checks:
        ensure_on_branch("release/sculptor-v*")
        ensure_clean_tree()

    prior_release_version = dev_semver()
    release_candidate_version = next_version(prior_release_version, VersionComponent.PRE_RELEASE)

    typer.echo(f"Incrementing the release to {release_candidate_version}.")

    # Verify there isn't a release tag and release branch for this.
    _run_out(["git", "fetch", "--tags"])
    _run_out(["git", "fetch"])

    if _run_pipe(["git", "tag", "--list", f"sculptor-v{release_candidate_version}"]).stdout:
        typer.echo("A release tag already exists for this version. Did you need to bump the version first?")
        raise typer.Exit(code=1)

    # Write the rc version to the pyproject.toml file.
    commit_new_version(None, release_candidate_version, dry_run=dry_run)

    typer.echo(
        f"About to trigger a new release branch for Sculptor {release_candidate_version} from git sha {dev_git_sha()}"
    )

    if not dry_run:
        push_tags(release_candidate_version)
        typer.secho("Tags have been pushed, and release will be kicked off", fg=typer.colors.GREEN)
    else:
        typer.secho("Would have released, but dry-run mode was enabled", fg=typer.colors.YELLOW)


@app.command("hotfix-release")
def hotfix_release(
    dry_run: bool = typer.Option(
        False,  # default → real upload
        "--dry-run/--no-dry-run",
        "-n",  # short alias for --dry-run
        help="Pass --dry-run (-n) to skip uploading or --no-dry-run to force the actual upload.",
    ),
    bypass_checks: bool = typer.Option(False, "--bypass-checks", help="Bypass branch protection checks"),
    dist_dir: Path = typer.Option("../dist", help="Directory that holds build artefacts."),
) -> None:
    """Patches a release that was promoted to production.

    Call this from an up-to-date branch of the most recently released Sculptor version. This will create a new patch
    branch.
    """
    old_version = dev_semver()

    if not bypass_checks:
        ensure_on_branch(f"release/{old_version}")
        ensure_clean_tree()

        if is_prerelease(old_version):
            typer.secho(
                "You cannot hotfix a pre-release version! Did you forget to release or do you need to git fetch?",
                err=True,
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=1)

    hotfix_release_version = next_version(old_version, VersionComponent.PATCH)

    typer.echo(f"Begining a hotfix branch for {hotfix_release_version}.")

    # Verify there isn't a release tag and release branch for this.
    _run_out(["git", "fetch", "--tags"])
    _run_out(["git", "fetch"])

    if _run_pipe(["git", "tag", "--list", f"sculptor-v{hotfix_release_version}"]).stdout:
        typer.echo("We already hotfixed this release! Do you need to update your hotfix target?")
        raise typer.Exit(code=1)

    if _run_pipe(["git", "branch", "--list", f"release/{hotfix_release_version}"]).stdout:
        typer.echo("We already attempted to hotfix this release! Do you need to switch your hotfix target?")
        typer.echo("A branch already exists for this version, but no release tag.")
        typer.echo("A prior release cut failed. Please delete the branch from origin and try again.")
        raise typer.Exit(code=1)

    # Write the rc version to the pyproject.toml file and begin a new branch.
    commit_new_version(f"release/sculptor-v{hotfix_release_version}", hotfix_release_version, dry_run=dry_run)

    typer.echo(f"Created a new hotfix branch for Sculptor {hotfix_release_version} from git sha {dev_git_sha()}")
    typer.echo("Now you must go and apply your fixups to that branch.")


@app.command("promote-release")
def promote_release(
    dry_run: bool = typer.Option(
        False,  # default → real upload
        "--dry-run/--no-dry-run",
        "-n",  # short alias for --dry-run
        help="Pass --dry-run (-n) to skip uploading or --no-dry-run to force the actual upload.",
    ),
    bypass_checks: bool = typer.Option(False, "--bypass-checks", help="Bypass branch protection checks"),
    dist_dir: Path = typer.Option("../dist", help="Directory that holds build artefacts."),
) -> None:
    """Promotes this release candidate version to a full release, and tags it.

    This initiates the process which will build and publish the release artifacts to all build targets.
    """

    release_version = next_version(dev_semver(), VersionComponent.STRIP_PRE_RELEASE)

    if not bypass_checks:
        ensure_on_branch(f"release/sculptor-v{release_version}")
        ensure_clean_tree()

        # Run git fetch, and abort if the release branch is BEHIND its upstream
        _run_out(["git", "fetch", "--prune"])

        status = _run_pipe(["git", "status", "--porcelain=2", "--branch"]).stdout
        for line in status.splitlines():
            if line.startswith("# branch.ab"):
                # The porcelain line looks like:
                # '# branch.ab +<ahead> -<behind>'
                _, _, _, behind_tok = line.split()

                behind = int(behind_tok.lstrip("-"))

                if behind > 0:
                    typer.secho(
                        "Your local release branch is behind the remote release branch. Please pull/rebase before continuing.",
                        fg=typer.colors.RED,
                    )
                    raise typer.Exit(code=1)
                break  # done once we've parsed the branch.ab line

    # Let's commit the new version to the current branch.
    commit_new_version(None, release_version, dry_run=dry_run)

    typer.echo(f"Releasing Sculptor {dev_semver()} from git sha {dev_git_sha()}")

    if not dry_run:
        push_tags(release_version)
        typer.secho("Tags have been pushed, and release will be kicked off.", fg=typer.colors.GREEN)
    else:
        typer.secho("Dry run: No tags were pushed")


@app.command("publish-build-artifacts")
def publish_build_artifacts(
    dry_run: bool = typer.Option(
        False,  # default → real upload
        "--dry-run/--no-dry-run",
        "-n",  # short alias for --dry-run
        help="Pass --dry-run (-n) to skip uploading or --no-dry-run to force the actual upload.",
    ),
    bypass_checks: bool = typer.Option(False, "--bypass-checks", help="Bypass branch protection checks"),
    dist_dir: Path = typer.Option("../dist", help="Directory that holds build artefacts."),
) -> None:
    """This command publishes _already built_ artifacts from s3 to the deployed buckets.

    Calling publish turns the artifacts that were already built live.

    You may only call publish after building has completed for _all_ artifacts, on every platform we support.
    """
    # We only publish the specific concrete version that is in the pyproject.toml file.
    release_version = dev_semver()

    if not bypass_checks:
        ensure_clean_tree()

    if is_prerelease(release_version):
        # If the release version is a release candidate, we're only going to upload to the alpha bucket
        stages = [BuildStage.ALPHA]
    else:
        stages = [BuildStage.ALPHA, BuildStage.STABLE]

    typer.secho(
        f"\nPublish was triggered to {[stage.value for stage in stages]} for Sculptor {release_version} from git sha {dev_git_sha()}",
        fg=typer.colors.YELLOW,
    )

    files_to_copy = []
    for stage in stages:
        for target in PLATFORM_ARCH_TO_TARGET.values():
            artifacts = artifacts_for_target_and_stage(target, stage)
            files_to_copy.extend(artifacts)

    if not bypass_checks:
        typer.echo("  • Verifying source artifacts exist in S3")
        are_artifacts_missing = False
        for artifact in files_to_copy:
            # Run s3 ls to verify the file exists
            try:
                _run_out(
                    [
                        "uvx",
                        "--from",
                        "awscli==1.41.12",
                        "--refresh",
                        "aws",
                        "s3",
                        "ls",
                        artifact.input_path,
                    ]
                )
            except subprocess.CalledProcessError:
                typer.secho(f"Source artifact not found: {artifact.input_path}", fg=typer.colors.RED)
                are_artifacts_missing = True
        if are_artifacts_missing:
            raise typer.Exit(code=1)

    if not dry_run:
        typer.echo("  • Publishing artifacts to release buckets")
        for artifact in files_to_copy:
            s3_copy(artifact.input_path, artifact.output_paths[0], dry_run=dry_run)

    else:
        typer.secho("Would have made the following copies, but dry-run mode was enabled.", fg=typer.colors.YELLOW)
        for artifact in files_to_copy:
            typer.secho(f"    {artifact!r}")


@app.command("snapshot-release-artifacts")
def snapshot_release_artifacts(
    platform: str = typer.Option("linux", "--platform", "-p"),
    arch: str = typer.Option("x86_64", "--arch", "-a"),
    dry_run: bool = typer.Option(
        False,
        "--dry-run/--no-dry-run",
        "-n",
        help="Pass --dry-run (-n) to skip uploading or --no-dry-run to force the actual upload.",
    ),
) -> None:
    """Puts the release artifacts for the given build in s3"""
    typer.echo(f"Staging release artifacts for platform={platform}, arch={arch}")

    target = PLATFORM_ARCH_TO_TARGET[platform, arch]
    stage = BuildStage.BUILT
    files = artifacts_for_target_and_stage(target, stage)
    typer.echo(f"Found artifacts to stage:\n  {files!r}")

    for artifact in files:
        for output_path in artifact.output_paths:
            s3_copy(artifact.input_path, output_path, dry_run=dry_run)


@app.command("retrieve-release-artifacts")
def retrieve_release_artifacts(
    platform: str = typer.Option("linux", "--platform", "-p"),
    arch: str = typer.Option("x64", "--arch", "-a"),
    version: str | None = typer.Option(None, "--version", "-v", help="The PEP440 Version to retrieve, e.g. v0.3.0"),
    dry_run: bool = typer.Option(
        False,
        "--dry-run/--no-dry-run",
        "-n",
        help="Pass --dry-run (-n) to skip retrieval or --no-dry-run to force the actual download.",
    ),
) -> None:
    """Pulls down the release artifacts for the current build from s3"""

    if version is not None:
        try:
            version_sha = _run_pipe(
                ["git", "show", "--pretty=%H", "-s", f"sculptor-v{version.lstrip('v')}"]
            ).stdout.strip()
        except subprocess.CalledProcessError:
            typer.secho(f"Could not find git tag for sculptor-v{version.lstrip('v')}", fg=typer.colors.RED)
            raise typer.Exit(code=1)
    else:
        version_sha = None

    typer.echo(f"About to retrieve release artifacts for platform={platform}, arch={arch}")

    target = PLATFORM_ARCH_TO_TARGET[platform, arch]
    stage = BuildStage.BUILT
    files = artifacts_for_target_and_stage(target, stage, version_override=version, git_sha_override=version_sha)
    typer.echo(f"Found artifacts to retrieve:\n  {files!r}")

    for artifact in files:
        output_path = artifact.output_paths[0]
        # Reverse the order of this copy--from the output path in s3 to local.
        s3_copy(source=output_path, destination=artifact.input_path, dry_run=dry_run)


@app.command("bump-version")
def bump_version(
    bypass_checks: bool = typer.Option(False, "--bypass-checks", help="Bypass branch protection checks"),
) -> None:
    """Bumps the version of Sculptor and creates an MR to Gitlab."""

    old_version = dev_semver()
    typer.echo(f"Current Sculptor version is {old_version}")

    bump_index = "Mmp".index(
        typer.prompt("Are you trying to bump a [M]ajor, [m]inor, or [p]atch version?", default="m")
    )
    new_version = next_version(old_version, VersionComponent(bump_index))
    typer.echo(f"The new Sculptor version will be {new_version}")

    if not bypass_checks:
        if bump_index in [0, 1]:
            # We're doing a regular release, from main
            ensure_on_branch("main")
        else:
            typer.secho(
                "You shouldn't bump the patch version, you probably want `just hotfix-release`.",
                err=True,
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=1)

        ensure_clean_tree()

    # New Branch for the MR
    branch_name = f"automated/bump-sculptor-v{new_version}"
    commit_new_version(branch_name, new_version)


def commit_new_version(branch_name: str | None, new_version: str, dry_run: bool = False) -> None:
    """Helper method to commit the new version to a new branch.

    Preconditions:
        - The working tree is clean.
    """

    if branch_name:
        # We want to create a new branch.
        _run_out(["git", "checkout", "-b", branch_name])

    write_project_version(new_version)
    repo_root_path = imbue_core.git.get_git_repo_root()

    _run_out(["uv", "lock"])

    _run_out(
        [
            "git",
            "add",
            str(repo_root_path / "sculptor" / "pyproject.toml"),
            str(repo_root_path / "uv.lock"),
        ]
    )

    _run_out(
        [
            "git",
            "commit",
            f"--message=Bumping Sculptor Version to v{new_version}",
        ]
    )

    if not dry_run:
        if branch_name:
            # Commit to the new branch.
            _run_out(["git", "push", "--set-upstream", "origin", branch_name])
        else:
            # Commit to the same branch.
            _run_out(["git", "push", "--set-upstream", "origin"])
    else:
        typer.echo(f"Would have pushed branch {branch_name} to origin, but dry-run mode was enabled.")
        typer.echo("Please remember to delete this branch before trying to take another cut.")


def write_project_version(new_version: str) -> None:
    """Helper method to write the updated project version to the pyproject.toml file."""
    pyproject = resources.files("sculptor").joinpath("../pyproject.toml")

    with resources.as_file(pyproject) as path, path.open("r") as f:
        config = tomlkit.load(f)

    project = config["project"]
    assert isinstance(project, Container)
    project["version"] = new_version

    with resources.as_file(pyproject) as path, path.open("w") as f:
        tomlkit.dump(config, f)


def push_tags(version: str) -> None:
    """Push a new tag with the given version to origin."""
    # Create a new release tag it and push it to origin.
    tagname = f"sculptor-v{version}"
    _run_out(["git", "tag", tagname])
    # No verify since this is only pushing a tag, and pyre can be finicky.
    _run_out(["git", "push", "origin", tagname, "--no-verify"])


def ensure_clean_tree() -> None:
    """Abort if the working tree has uncommitted changes."""
    if _run_pipe(["git", "status", "--porcelain"]).stdout.strip():
        typer.secho(
            "Working directory is dirty – commit or stash changes first.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)


def ensure_on_branch(*expected_names: str) -> None:
    """Abort unless HEAD is on *expected* branch.

    Supports wildcard expressions such as "release/*"
    """
    if not expected_names:
        expected_names = ("main",)

    current = _run_pipe(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    if not any(fnmatch.fnmatch(current, expected_name) for expected_name in expected_names):
        typer.secho(
            f"Your branch must match {expected_names!r}. (current: {current!r}).",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)


@app.command("create-version-file")
def create_version_file() -> None:
    """Create a version file with the Sculptor version and Git SHA."""
    sculptor_version = dev_semver()
    sha = dev_git_sha()
    with open("sculptor/_version.py", "w") as f:
        f.write(
            f'"""Sculptor v{sculptor_version} version file, autogenerated by the build process.\nDo not edit."""\n'
        )
        f.write(f"__version__ = '{sculptor_version}'\n")
        f.write(f"__git_sha__ = '{sha}'\n")


@app.command("sync-frontend-version")
def sync_frontend_version(
    reverse: bool = typer.Option(False, "--reverse", "-r", help="Reset frontend package.json version to 0.0.0"),
) -> None:
    """Sync frontend package.json version with sculptor pyproject.toml version, or reset to 0.0.0 with --reverse."""
    frontend_package_json_path = Path("frontend/package.json")

    if not frontend_package_json_path.exists():
        typer.secho(f"Frontend package.json not found at {frontend_package_json_path}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    # Read current package.json
    with frontend_package_json_path.open("r") as f:
        package_data = json.load(f)

    # Determine target version
    old_version = package_data.get("version", "unknown")
    if reverse:
        target_version = "0.0.0"
        action = "Reset"
    else:
        target_version = pep_440_to_semver(dev_semver())
        action = "Updated"

    package_data["version"] = target_version

    # Write back to package.json
    with frontend_package_json_path.open("w") as f:
        json.dump(package_data, f, indent=2)
        f.write("\n")  # Add final newline for consistency

    typer.secho(f"{action} frontend package.json version: {old_version} → {target_version}", fg=typer.colors.GREEN)


@app.command("generate-autoupdate-manifest")
def generate_autoupdate_manifest(operating_system: str, architecture: str) -> None:
    """Generate the autoupdate manifest for Sculptor's packages.

    operating_system must be one of "macos" or "linux".
    architecture must be one of "amd64" or "arm64".
    """
    match (operating_system, architecture):
        case ("macos", "arm64"):
            _generate_autoupdate_manifest("latest-mac.yml", "zip", "arm64")
        case ("macos", "amd64"):
            _generate_autoupdate_manifest("latest-mac.yml", "zip", "amd64")
        case ("linux", "arm64"):
            _generate_autoupdate_manifest("latest-linux.yml", "AppImage", "arm64")
        case ("linux", "amd64"):
            _generate_autoupdate_manifest("latest-linux.yml", "AppImage", "amd64")
        case _ as never:
            typer.secho(
                "".join(
                    [
                        f"Invalid operating_system/architecture combination: {operating_system}/{architecture}. ",
                        "Must be one of: macos/amd64, macos/arm64, linux/amd64, linux/arm64.",
                    ]
                ),
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=1)


def _generate_autoupdate_manifest(
    out_filename: str,
    package_extension: str,
    architecture: Literal["amd64", "arm64"],
) -> None:
    # This function is called from sculptor/frontend, and returns generally_intelligent/dist/zip
    pkg_artifact_dir = Path.cwd() / ".." / ".." / "dist"
    if not pkg_artifact_dir.exists():
        typer.secho(
            f"Package artifact directory not found at {pkg_artifact_dir}. Please run just pkg", fg=typer.colors.RED
        )
        raise typer.Exit(code=1)

    # We should search recursively for all the descendant .zip files
    pkg_files = list(pkg_artifact_dir.rglob(f"*.{package_extension}"))
    if not pkg_files:
        typer.secho(f"No .{package_extension} files found in {pkg_artifact_dir}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    app_version = pep_440_to_semver(dev_semver())

    if len(pkg_files) != 1:
        # The cheese stands alone
        typer.secho(
            f"Cannot proceed with multiple .{package_extension} files in {pkg_artifact_dir}", fg=typer.colors.RED
        )
        raise typer.Exit(code=1)

    for pkg_file in pkg_files:
        typer.echo(f"Generating {out_filename} for version {app_version} from {pkg_file.name}")

        file_buffer = pkg_file.read_bytes()
        sha512_b64 = base64.b64encode(hashlib.sha512(file_buffer).digest()).decode("ascii")
        file_size = pkg_file.stat().st_size

        # Get the CloudFront CDN URLs for the control plane and default devcontainer for the specific architecture
        control_plane_url = ControlPlaneImageNameProvider(
            predetermined_run_mode=ControlPlaneRunMode.TAGGED_RELEASE, predetermined_platform_architecture=architecture
        ).determine_control_plane_image_name()
        default_devcontainer_url = get_default_devcontainer_image_reference()

        release_date = datetime.now(timezone.utc).isoformat()
        # Docker images use placeholder values for sha512 and size:
        # - electron-updater only validates the main installer (zip/AppImage), not additional files
        # - The TypeScript auto-updater uses these URLs for predownload but doesn't validate them
        # - The actual download happens via CDN, with runtime checks to skip if already cached
        # - Empty sha512 and size=0 are acceptable for informational entries in the manifest
        yaml_content = f"""version: {app_version}
files:
  - url: {pkg_file.name}
    sha512: {sha512_b64}
    size: {file_size}
  - url: "{control_plane_url}"
    sha512: ""
    size: 0
  - url: "{default_devcontainer_url}"
    sha512: ""
    size: 0
releaseDate: {release_date}
"""
        yaml_path = pkg_file.parent / out_filename
        yaml_path.write_text(yaml_content)
        typer.secho(f"Generated {yaml_path} for electron-updater.", fg=typer.colors.GREEN)


def s3_copy(source: str, destination: str, dry_run: bool = False) -> None:
    """Uses the s3 CLI cp command to copy a file to s3.

    Either source or destination may be local filepaths or s3 uris
    """
    cmd_base = ["uvx", "--from", "awscli==1.41.12", "--refresh", "aws", "s3", "cp"]
    if dry_run:
        cmd_base.append("--dryrun")

    _run_out([*cmd_base, source, destination])


@app.command("validate-darwin-binary")
def validate_darwin_binary(
    binary_path: Path = typer.Argument(..., help="Path to the macOS binary to validate."),
    arch: str = typer.Argument(..., help="Architecture of the binary (e.g., x86_64, arm64)."),
) -> None:
    """Given a file within the macOs App Bundle, this performs various validations."""

    if not darwin.validate_binary(binary_path=str(binary_path), arch=arch):
        typer.secho(f"Validation failed for binary at {binary_path}", fg=typer.colors.RED)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
