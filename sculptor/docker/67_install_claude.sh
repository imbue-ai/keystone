#!/bin/bash
set -e
set -u
set -x

# Install Claude CLI
# Check if claude is already installed

if command -v claude >/dev/null 2>&1; then
    echo "Claude CLI already installed"
    claude --version
    exit 0
fi

# Install Node.js if not already installed (Claude CLI is distributed via npm)
if ! command -v node >/dev/null 2>&1; then
    echo "Installing Node.js..."
    # TODO: Install nvm and call "nvm use" for the proper fix.
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y 'nodejs=20.13.1-*'
fi

# Install Claude CLI via npm

echo "Installing Claude CLI..."
sudo npm install -g @anthropic-ai/claude-code@1.0.27

# Verify installation
claude --version

echo "Claude CLI installed successfully"
