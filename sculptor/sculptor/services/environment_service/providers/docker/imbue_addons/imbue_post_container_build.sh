#!/usr/bin/env bash

# The imbue control plane in /nix/... and /imbue/... is not available during image build
# since we mount it as a volume into the container.

# This script is meant to run after the container has access to the imbue control plane.

# This script needs to be run as root.

set -euo pipefail
set -x

# Assert that this script is being run as root
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root" >&2
    exit 1
fi

# Read the container user from the file created during build.
CONTAINER_USER=$(cat /imbue_addons/container_user.txt)
USER_HOME=$(cat /imbue_addons/container_user_home.txt)
USER_GROUP=$(cat /imbue_addons/container_user_group.txt)

echo "Running post-container build setup for user: ${CONTAINER_USER}:${USER_GROUP} with home: ${USER_HOME}"

# Unlock the user for SSH.
# Otherwise, you might see:
# bash-5.3# cat /tmp/sshd_log.txt | grep locked
# User test_user not allowed because account is locked
/imbue/nix_bin/usermod -p '*' ${CONTAINER_USER}

# Must be root, otherwise: "could not lock config file /etc/gitconfig: Permission denied"
/imbue/nix_bin/git config --system --add safe.directory /user_home/workspace
/imbue/nix_bin/echo "Added /user_home/workspace to git safe.directory"

# Set diff3 conflict style if not already set or if it's the default "merge"
current_style=$(/imbue/nix_bin/git config --system merge.conflictstyle || true)
if [ -z "$current_style" ]; then
    /imbue/nix_bin/git config --system merge.conflictstyle diff3
    /imbue/nix_bin/echo "Set git merge.conflictstyle system to diff3: /etc/gitconfig is"
    /imbue/nix_bin/cat /etc/gitconfig
fi

# Disable git garbage collection to minimize snapshot size
/imbue/nix_bin/git config --system gc.auto 0
/imbue/nix_bin/echo "Disabled git garbage collection (gc.auto = 0)"

########################### SSHD SETUP ###########################
set -euo pipefail
set -x

if ! id -u sshd > /dev/null 2>&1; then
    echo "Adding sshd user."
    useradd -r -s /usr/sbin/nologin sshd
else
    echo "sshd user already exists."
fi

echo "Making /var/empty for sshd."
mkdir -p /var/empty
chown root:root /var/empty
chmod 700 /var/empty

echo "Making host keys for sshd."
mkdir -p /sshd_config

# TODO: Move this directory to /imbue_addons/sshd_config/
# shellcheck disable=SC2046
yes n | ssh-keygen -t ed25519 -f /sshd_config/ssh_host_ed25519_key -N "" || true

echo "Making sshd config."
echo "Setting up ssh config."
# shellcheck disable=SC2046
SFTP_SERVER_PATH=$(readlink -f $(find $(dirname $(readlink -f /imbue/nix_bin/sshd))/../ -executable -name sftp-server | head -n 1))
cat > /sshd_config/sshd_config <<EOF
Port 2222
PermitRootLogin yes           # Only in containers / test environments
PermitUserEnvironment yes

PubkeyAuthentication yes
PasswordAuthentication no
ChallengeResponseAuthentication no
UsePAM no

AuthorizedKeysFile ${USER_HOME}/.ssh/authorized_keys
HostKey /sshd_config/ssh_host_ed25519_key

PidFile /var/run/sshd.pid
LogLevel INFO
Subsystem sftp $SFTP_SERVER_PATH
EOF

# Configure tmux to save command history
mkdir -p "${USER_HOME}/tmux-session-logs"
chown -R "${CONTAINER_USER}:${USER_GROUP}" "${USER_HOME}/tmux-session-logs"
cat >> "${USER_HOME}/.bashrc" <<EOF
if [ -n "\$TMUX" ]; then
    /imbue/nix_bin/tmux pipe-pane "cat >> ${USER_HOME}/tmux-session-logs/tmux_session_#S_#I_#P.log" 2> /dev/null
fi

# Indicate in the prompt if the last command failed
DEFAULT_PROMPT=\$PS1
function set_my_prompt {
    local LAST_EXIT_CODE=\$?
    if [ "\$LAST_EXIT_CODE" -ne 0 ]; then
        PS1="[Exit:\$LAST_EXIT_CODE] \$DEFAULT_PROMPT"
    else
        PS1="\$DEFAULT_PROMPT"
    fi
}
PROMPT_COMMAND=set_my_prompt
EOF

# Ensure agent data directory ownership is correct
# This is not recursive because that directory is quite large, and it would take a while to recurse through it.
echo "Setting open permissions on /agent/data because this folder is written to from many different agents, which might have different Linux users."
/imbue/nix_bin/chmod a+rwX /agent/data

echo "Post-container start setup complete."
