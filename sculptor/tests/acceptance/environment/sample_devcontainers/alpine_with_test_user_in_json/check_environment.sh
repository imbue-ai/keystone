#!/bin/sh

set -e

cat /alpine_test_user_json.txt

echo "=== Alpine with test_user (from JSON) Environment Check ==="
echo "- Alpine version:"
cat /etc/alpine-release

echo "- Container user (should be test_user from remoteUser in devcontainer.json):"
cat /imbue_addons/container_user.txt

# Verify the container user is test_user
CONTAINER_USER=$(cat /imbue_addons/container_user.txt)
if [ "$CONTAINER_USER" != "test_user" ]; then
    echo "❌ ERROR: Expected container user to be test_user, but got $CONTAINER_USER"
    exit 1
fi

echo "- Current running user:"
whoami

echo "- User home directory:"
echo $HOME

echo "- Basic tools:"
which sh && sh --help | head -1 || echo "sh available"
which ls && ls --version | head -1 || echo "ls available"

echo "✅ Alpine with test_user (from JSON) environment checks passed!"
