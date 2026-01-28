#!/imbue/nix_bin/bash
# shellcheck shell=bash
#
# PreToolUse hook that blocks tool execution until environment is ready.
#
# This script is called by Claude Code before executing any tool.
# It waits for the ready indicator file to appear.
#
# Usage: bash check_tool_readiness.sh <ready_file_path>
#
# Arguments:
#   ready_file_path: Path to the ready indicator file
#
# Expected environment variables:
#   SCULPTOR_TOOL_READINESS_TIMEOUT: Max seconds to wait (default: 120)
#
# Exit codes:
#   0: Environment ready, allow tool execution
#   2: Timeout, block tool execution
#
# Output format (JSON to stdout for success, stderr for error):
#   Success: {"decision": "allow"}
#   Error: {"decision": "deny", "reason": "..."}
#
# Note: This uses polling rather than inotifywait. If we decide the performance
# benefit is worth the additional dependency, we could add inotify-tools to the
# control plane and use: inotifywait -t $TIMEOUT -e create -qq $(dirname "$READY_FILE")

set -euo pipefail

READY_FILE="${1:-}"
if [ -z "$READY_FILE" ]; then
    echo '{"decision": "deny", "reason": "check_tool_readiness.sh: ready file path argument is required"}' >&2
    exit 2
fi

TIMEOUT="${SCULPTOR_TOOL_READINESS_TIMEOUT:-120}"

# Wait loop - check every 0.1s using bash arithmetic
# We track iterations rather than elapsed time to avoid floating point
CHECK_INTERVAL_MS=100
MAX_ITERATIONS=$((TIMEOUT * 1000 / CHECK_INTERVAL_MS))

for ((i=0; i<MAX_ITERATIONS; i++)); do
    if [ -f "$READY_FILE" ]; then
        # Environment ready, allow tool execution
        echo '{"decision": "allow"}'
        exit 0
    fi
    sleep 0.1
done

# Timeout - return blocking error with helpful message
error_msg="Environment setup is taking longer than expected (timeout after ${TIMEOUT}s)"
echo "{\"decision\": \"deny\", \"reason\": \"$error_msg\"}" >&2
exit 2
