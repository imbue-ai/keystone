"""Tests for the target module."""

import pytest
from builder.artifacts import ArtifactFile
from builder.artifacts import BuildStage
from builder.artifacts import Target
from builder.artifacts import artifacts_for_target_and_stage

from sculptor import version

GIT_SHA = version.dev_git_sha(short=False)
VERSION = version.dev_semver()
PEDANTIC_VERSION = version.pep_440_to_semver(VERSION)


@pytest.mark.parametrize(
    ["target", "stage", "expected_artifact_files"],
    [
        # ---------------- LINUX X64 ----------------
        (
            Target.LINUX_X64,
            BuildStage.ALPHA,
            [
                (
                    f"s3://imbue-sculptor-builds/{GIT_SHA}/AppImage/x64/Sculptor.AppImage",
                    ["s3://imbue-sculptor-releases/sculptor-alpha/AppImage/x64/Sculptor.AppImage"],
                ),
                (
                    f"s3://imbue-sculptor-builds/{GIT_SHA}/AppImage/x64/latest-linux.yml",
                    ["s3://imbue-sculptor-releases/sculptor-alpha/AppImage/x64/latest-linux.yml"],
                ),
            ],
        ),
        (
            Target.LINUX_X64,
            BuildStage.STABLE,
            [
                (
                    f"s3://imbue-sculptor-builds/{GIT_SHA}/AppImage/x64/Sculptor.AppImage",
                    ["s3://imbue-sculptor-releases/sculptor/AppImage/x64/Sculptor.AppImage"],
                ),
                (
                    f"s3://imbue-sculptor-builds/{GIT_SHA}/AppImage/x64/latest-linux.yml",
                    ["s3://imbue-sculptor-releases/sculptor/AppImage/x64/latest-linux.yml"],
                ),
            ],
        ),
        (
            Target.LINUX_X64,
            BuildStage.BUILT,
            [
                (
                    "../dist/AppImage/x64/Sculptor.AppImage",
                    [f"s3://imbue-sculptor-builds/{GIT_SHA}/AppImage/x64/Sculptor.AppImage"],
                ),
                (
                    "../dist/AppImage/x64/latest-linux.yml",
                    [f"s3://imbue-sculptor-builds/{GIT_SHA}/AppImage/x64/latest-linux.yml"],
                ),
            ],
        ),
        # ---------------- MAC ARM64 ----------------
        (
            Target.MAC_ARM64,
            BuildStage.ALPHA,
            [
                (
                    f"s3://imbue-sculptor-builds/{GIT_SHA}/Sculptor.dmg",
                    [
                        "s3://imbue-sculptor-releases/sculptor-alpha/Sculptor.dmg",
                        f"s3://imbue-sculptor-releases/sculptor-alpha/Sculptor-{VERSION}.dmg",
                    ],
                ),
                (
                    f"s3://imbue-sculptor-builds/{GIT_SHA}/zip/darwin/arm64/Sculptor-darwin-arm64-{PEDANTIC_VERSION}.zip",
                    [
                        f"s3://imbue-sculptor-releases/sculptor-alpha/zip/darwin/arm64/Sculptor-darwin-arm64-{VERSION}.zip"
                    ],
                ),
                (
                    f"s3://imbue-sculptor-builds/{GIT_SHA}/zip/darwin/arm64/latest-mac.yml",
                    ["s3://imbue-sculptor-releases/sculptor-alpha/zip/darwin/arm64/latest-mac.yml"],
                ),
            ],
        ),
        (
            Target.MAC_ARM64,
            BuildStage.STABLE,
            [
                (
                    f"s3://imbue-sculptor-builds/{GIT_SHA}/Sculptor.dmg",
                    [
                        "s3://imbue-sculptor-releases/sculptor/Sculptor.dmg",
                        f"s3://imbue-sculptor-releases/sculptor/Sculptor-{VERSION}.dmg",
                    ],
                ),
                (
                    f"s3://imbue-sculptor-builds/{GIT_SHA}/zip/darwin/arm64/Sculptor-darwin-arm64-{VERSION}.zip",
                    [f"s3://imbue-sculptor-releases/sculptor/zip/darwin/arm64/Sculptor-darwin-arm64-{VERSION}.zip"],
                ),
                (
                    f"s3://imbue-sculptor-builds/{GIT_SHA}/zip/darwin/arm64/latest-mac.yml",
                    ["s3://imbue-sculptor-releases/sculptor/zip/darwin/arm64/latest-mac.yml"],
                ),
            ],
        ),
        (
            Target.MAC_ARM64,
            BuildStage.BUILT,
            [
                (
                    "../dist/Sculptor.dmg",
                    [
                        f"s3://imbue-sculptor-builds/{GIT_SHA}/Sculptor.dmg",
                        f"s3://imbue-sculptor-builds/{GIT_SHA}/Sculptor-{VERSION}.dmg",
                    ],
                ),
                (
                    f"../dist/zip/darwin/arm64/Sculptor-darwin-arm64-{VERSION}.zip",
                    [f"s3://imbue-sculptor-builds/{GIT_SHA}/zip/darwin/arm64/Sculptor-darwin-arm64-{VERSION}.zip"],
                ),
                (
                    "../dist/zip/darwin/arm64/latest-mac.yml",
                    [f"s3://imbue-sculptor-builds/{GIT_SHA}/zip/darwin/arm64/latest-mac.yml"],
                ),
            ],
        ),
        # ---------------- MAC X64 ----------------
        (
            Target.MAC_X64,
            BuildStage.ALPHA,
            [
                (
                    f"s3://imbue-sculptor-builds/{GIT_SHA}/Sculptor-x86_64.dmg",
                    [
                        "s3://imbue-sculptor-releases/sculptor-alpha/Sculptor-x86_64.dmg",
                        f"s3://imbue-sculptor-releases/sculptor-alpha/Sculptor-x86_64-{VERSION}.dmg",
                    ],
                ),
                (
                    f"s3://imbue-sculptor-builds/{GIT_SHA}/zip/darwin/x64/Sculptor-darwin-x64-{VERSION}.zip",
                    [f"s3://imbue-sculptor-releases/sculptor-alpha/zip/darwin/x64/Sculptor-darwin-x64-{VERSION}.zip"],
                ),
                (
                    f"s3://imbue-sculptor-builds/{GIT_SHA}/zip/darwin/x64/latest-mac.yml",
                    ["s3://imbue-sculptor-releases/sculptor-alpha/zip/darwin/x64/latest-mac.yml"],
                ),
            ],
        ),
        (
            Target.MAC_X64,
            BuildStage.STABLE,
            [
                (
                    f"s3://imbue-sculptor-builds/{GIT_SHA}/Sculptor-x86_64.dmg",
                    [
                        "s3://imbue-sculptor-releases/sculptor/Sculptor-x86_64.dmg",
                        f"s3://imbue-sculptor-releases/sculptor/Sculptor-x86_64-{VERSION}.dmg",
                    ],
                ),
                (
                    f"s3://imbue-sculptor-builds/{GIT_SHA}/zip/darwin/x64/Sculptor-darwin-x64-{VERSION}.zip",
                    [f"s3://imbue-sculptor-releases/sculptor/zip/darwin/x64/Sculptor-darwin-x64-{VERSION}.zip"],
                ),
                (
                    f"s3://imbue-sculptor-builds/{GIT_SHA}/zip/darwin/x64/latest-mac.yml",
                    ["s3://imbue-sculptor-releases/sculptor/zip/darwin/x64/latest-mac.yml"],
                ),
            ],
        ),
        (
            Target.MAC_X64,
            BuildStage.BUILT,
            [
                (
                    "../dist/Sculptor-x86_64.dmg",
                    [
                        f"s3://imbue-sculptor-builds/{GIT_SHA}/Sculptor-x86_64.dmg",
                        f"s3://imbue-sculptor-builds/{GIT_SHA}/Sculptor-x86_64-{VERSION}.dmg",
                    ],
                ),
                (
                    f"../dist/zip/darwin/x64/Sculptor-darwin-x64-{PEDANTIC_VERSION}.zip",
                    [f"s3://imbue-sculptor-builds/{GIT_SHA}/zip/darwin/x64/Sculptor-darwin-x64-{VERSION}.zip"],
                ),
                (
                    "../dist/zip/darwin/x64/latest-mac.yml",
                    [f"s3://imbue-sculptor-builds/{GIT_SHA}/zip/darwin/x64/latest-mac.yml"],
                ),
            ],
        ),
    ],
)
def test_artifacts_for_target_stage(target, stage, expected_artifact_files):
    """Verifies that we load the correct files for a particular platform and arch."""

    expected = []
    for input_path, output_paths in expected_artifact_files:
        expected.append(ArtifactFile(input_path=input_path, output_paths=output_paths))

    assert artifacts_for_target_and_stage(target=target, build_stage=stage) == expected
