#!/bin/sh
# Setup script for Imbue control plane user configuration.
#
# IMPORTANT: This script is run during Docker image build from many different host systems
# (Alpine, Ubuntu, Debian, etc.) WITHOUT access to the Imbue control plane.
# Therefore it must:
# - Use only POSIX-compliant shell syntax (#!/bin/sh, not bash)
# - Use only basic commands available on all systems (cat, echo, mkdir, chmod, chown, cut, grep)
# - NOT rely on /imbue or /nix being available (those are mounted later at container runtime)

set -euo

# =============================================================================
# Determine container user and group
# =============================================================================

CONTAINER_USER=$(cat /imbue_addons/container_user.txt)
echo "Configuring Imbue setup for user: ${CONTAINER_USER}"

USER_HOME=$(su - "${CONTAINER_USER}" -c pwd)
USER_GROUP=$(id -gn "${CONTAINER_USER}")

if [ -z "${USER_HOME}" ]; then
    echo "ERROR: Could not determine home directory for user ${CONTAINER_USER}"
    exit 1
fi

if [ -z "${USER_GROUP}" ]; then
    echo "ERROR: Could not determine group name for user ${CONTAINER_USER}"
    exit 1
fi

echo "User home directory: ${USER_HOME}"
echo "User primary group: ${USER_GROUP}"

# Save these values to files for use by Dockerfile and other scripts
echo "${USER_HOME}" > /imbue_addons/container_user_home.txt
echo "${USER_GROUP}" > /imbue_addons/container_user_group.txt

# =============================================================================
# Set up user home directory
# =============================================================================

# Create and configure .ssh directory
mkdir -p "${USER_HOME}/.ssh"
chmod 700 "${USER_HOME}/.ssh"

# Set up SSH environment with Imbue control plane PATH
cat > "${USER_HOME}/.ssh/environment" <<EOF
PATH=${_IMBUE_CONTROL_PLANE_PATH}
EOF

# Set up SSH keys
cp -r /tmp/ssh_keys/* "${USER_HOME}/.ssh/"
cat "${USER_HOME}/.ssh/id_rsa.pub" >> "${USER_HOME}/.ssh/authorized_keys"
chmod 600 "${USER_HOME}/.ssh/authorized_keys"
rm -rf /tmp/ssh_keys

# Set ownership of .ssh directory
chown -R "${CONTAINER_USER}:${USER_GROUP}" "${USER_HOME}/.ssh"

# Create tmux configuration
cat > "${USER_HOME}/.tmux.conf" <<EOF
set -g status-style bg=#ADDDC0,fg=#3B352B
set -g default-shell "/imbue_addons/bash_with_user_env.sh"
EOF
chown "${CONTAINER_USER}:${USER_GROUP}" "${USER_HOME}/.tmux.conf"

# Set up .profile with Imbue control plane PATH,
# So that when we SSH into the container, we have the control_plane PATH.
cat >> "${USER_HOME}/.profile" <<EOF
export PATH=${_IMBUE_CONTROL_PLANE_PATH}
EOF
chown "${CONTAINER_USER}:${USER_GROUP}" "${USER_HOME}/.profile"

# =============================================================================
# Set up /imbue_addons directory
# =============================================================================

# Set up agent_path_extension_bin directory
mkdir -p /imbue_addons/agent_path_extension_bin

# Create symlinks (will be valid once control plane is mounted)
ln -s /imbue/nix_bin/bash /imbue_addons/agent_path_extension_bin/bash

# Make the copied binaries executable
chmod a+rx /imbue_addons/agent_path_extension_bin/claude /imbue_addons/agent_path_extension_bin/imbue-cli.sh

# Create necessary directories
mkdir -p "/imbue_addons/artifacts"
mkdir -p "/imbue_addons/state"

# Set ownership of /imbue_addons directory
chown -R "${CONTAINER_USER}:${USER_GROUP}" /imbue_addons

# =============================================================================
# Set up system directories
# =============================================================================

# Ensure sshd can run
mkdir -p /run/sshd
chmod 755 /run/sshd

# Create /nix via symlink. The target isn't available yet because the volume isn't mounted, but that's okay.
# -s: --symbolic (but this does not work in Alpine.)
# -T: --no-target-directory (the target cannot be a directory in which to create the thing.)
ln -s -T /imbue/nix /nix || { echo "Imbue's control plane won't currently work if the image already contains /nix"; false; }

# =============================================================================
# Done
# =============================================================================

echo "Imbue user configuration complete for ${CONTAINER_USER}:${USER_GROUP}"
