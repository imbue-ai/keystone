#!/usr/bin/env bash
# This script runs in our CI to execute acceptance tests for Sculptor Desktop after it has been packaged.
#
# This will implicitly test the packaged version of sculptor for the current SHA.
#
# Usage: test-sculptor-desktop-acceptance.sh platform architecture
set -euo pipefail
set -x

uv run builder retrieve-release-artifacts -p "$1" -a "$2"


# If we are running on macOS, we want to put the APP artifacts in ../dist/out so that the acceptance test code runs perfectly.
# NOTE that we use zip rather than DMGs because they are more deterministic than the DMG.
if [[ "$1" == "darwin" ]]; then
    if [[ "$2" == "arm64" ]]; then
        unzip -q ../dist/zip/darwin/arm64/Sculptor-darwin-arm64-*.zip -d ../dist
    else
        unzip -q ../dist/zip/darwin/x64/Sculptor-darwin-x64-*.zip -d ../dist
    fi
fi

# If we are running on Linux, we extract the AppImage to /dist as well
if [[ "$1" == "linux" ]]; then
    img=../dist/AppImage/x64/Sculptor.AppImage
    chmod +x "$img"
    pushd "$(dirname "$img")" >/dev/null

    # Clean any previous extraction
    rm -rf squashfs-root
    "./$(basename "$img")" --appimage-extract
    rsync -a --delete squashfs-root/ ../dist/
    popd >/dev/null
fi

# Run pytest for acceptance tests, but always return true to avoid failing the CI job
uv run pytest ./tests/integration ./tests/acceptance -m "app-electron and acceptance" --show-capture=all --capture=tee-sys -v -ra "${@:3}" || true
