#!/bin/bash

set -euo pipefail
set -x

# Default devcontainer should have sudo access.
sudo touch /i_can_sudo.txt

python3 --version
uv --version
git --version

echo "✅ Default Devcontainer environment checks passed!"
