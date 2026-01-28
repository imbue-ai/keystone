#!/usr/bin/env bash

# Upgrade script for containers created with older versions of Sculptor.
#
# This script upgrades containers that were created with an older version of Sculptor
# to appear as though they were created by the current version of Sculptor.
#
# This script needs to be run as root and requires access to the Imbue control plane.

set -euo pipefail
set -x

# Assert that this script is being run as root
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root" >&2
    exit 1
fi

echo "Running Imbue container upgrade script..."

# =============================================================================
# Check and create container user metadata files
# =============================================================================

# If container_user.txt doesn't exist, we're upgrading from an old container
if [ ! -f /imbue_addons/container_user.txt ]; then
    echo "Upgrading container: creating container user metadata files..."

    # Old containers always used sculptoruser
    echo "sculptoruser" > /imbue_addons/container_user.txt
    echo "sculptoruser" > /imbue_addons/container_user_group.txt
    echo "/home/sculptoruser" > /imbue_addons/container_user_home.txt

    # Set ownership of the metadata files to sculptoruser
    /imbue/nix_bin/chown sculptoruser:sculptoruser /imbue_addons/container_user.txt
    /imbue/nix_bin/chown sculptoruser:sculptoruser /imbue_addons/container_user_group.txt
    /imbue/nix_bin/chown sculptoruser:sculptoruser /imbue_addons/container_user_home.txt

    echo "Created container user metadata files"
else
    echo "Container user metadata files already exist - no upgrade needed for this component"
fi

# Read the container user information
CONTAINER_USER=$(cat /imbue_addons/container_user.txt)
USER_HOME=$(cat /imbue_addons/container_user_home.txt)
USER_GROUP=$(cat /imbue_addons/container_user_group.txt)

echo "Container user: ${CONTAINER_USER}:${USER_GROUP} with home: ${USER_HOME}"

# =============================================================================
# Migrate state and artifacts directories
# =============================================================================

# Check if artifacts directory exists in /imbue_addons
if [ ! -e /imbue_addons/artifacts ]; then
    echo "Upgrading container: handling artifacts directory..."

    # Check if old location exists
    if [ -d "${USER_HOME}/artifacts" ]; then
        echo "Found artifacts in old location (${USER_HOME}/artifacts), creating symlink in /imbue_addons"
        /imbue/nix_bin/ln -s "${USER_HOME}/artifacts" /imbue_addons/artifacts
    else
        echo "No artifacts directory found in old location, creating new one in /imbue_addons"
        /imbue/nix_bin/mkdir -p /imbue_addons/artifacts
        /imbue/nix_bin/chown "${CONTAINER_USER}:${USER_GROUP}" /imbue_addons/artifacts
    fi
else
    echo "artifacts directory already exists in /imbue_addons - no upgrade needed"
fi

# Check if state directory exists in /imbue_addons
if [ ! -e /imbue_addons/state ]; then
    echo "Upgrading container: handling state directory..."

    # Check if old location exists
    if [ -d "${USER_HOME}/state" ]; then
        echo "Found state in old location (${USER_HOME}/state), creating symlink in /imbue_addons"
        /imbue/nix_bin/ln -s "${USER_HOME}/state" /imbue_addons/state
    else
        echo "No state directory found in old location, creating new one in /imbue_addons"
        /imbue/nix_bin/mkdir -p /imbue_addons/state
        /imbue/nix_bin/chown "${CONTAINER_USER}:${USER_GROUP}" /imbue_addons/state
    fi
else
    echo "state directory already exists in /imbue_addons - no upgrade needed"
fi

# =============================================================================
# Fix /imbue_addons ownership
# =============================================================================

# Ensure /imbue_addons and its contents are owned by the container user
# This allows non-root processes to create directories and files in /imbue_addons
echo "Ensuring /imbue_addons is owned by ${CONTAINER_USER}:${USER_GROUP}..."
/imbue/nix_bin/chown -R "${CONTAINER_USER}:${USER_GROUP}" /imbue_addons

# =============================================================================
# Done
# =============================================================================

echo "Imbue container upgrade complete."
