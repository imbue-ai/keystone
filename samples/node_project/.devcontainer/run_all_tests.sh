#!/bin/bash
set -euo pipefail

# Ensure test artifacts directory exists
mkdir -p /test_artifacts

# Track overall success
OVERALL_SUCCESS=true

# Function to run a test command and capture outputs
run_test() {
    local name=$1
    shift
    local cmd=("$@")

    echo "================================================================================"
    echo "Running: ${name}"
    echo "Command: ${cmd[*]}"
    echo "================================================================================"

    local test_dir="/test_artifacts/${name}"
    mkdir -p "${test_dir}"

    local stdout_file="${test_dir}/stdout.txt"
    local stderr_file="${test_dir}/stderr.txt"

    # Add timestamp
    echo "Started: $(date -Iseconds)" | tee "${stdout_file}"
    echo "Started: $(date -Iseconds)" | tee "${stderr_file}"

    # Run the command and capture output
    if "${cmd[@]}" > >(tee -a "${stdout_file}") 2> >(tee -a "${stderr_file}" >&2); then
        echo "✓ ${name} passed" | tee -a "${stdout_file}"
        echo "Finished: $(date -Iseconds)" | tee -a "${stdout_file}"
        return 0
    else
        echo "✗ ${name} failed" | tee -a "${stderr_file}" >&2
        echo "Finished: $(date -Iseconds)" | tee -a "${stderr_file}"
        OVERALL_SUCCESS=false
        return 1
    fi
}

# Run Node.js tests
echo "Starting test suite execution..."

# Run tests with TAP reporter (machine-readable and human-readable)
# TAP output is saved to a file for structured parsing
run_test "node-tests" bash -c "node --test 2>&1 | tee /test_artifacts/node-test-report.json" || true

# Create final result JSON
if [ "$OVERALL_SUCCESS" = true ]; then
    cat > /test_artifacts/final_result.json <<EOF
{
  "success": true,
  "timestamp": "$(date -Iseconds)",
  "message": "All tests passed"
}
EOF
    echo ""
    echo "================================================================================"
    echo "✓ ALL TESTS PASSED"
    echo "================================================================================"
    exit 0
else
    cat > /test_artifacts/final_result.json <<EOF
{
  "success": false,
  "timestamp": "$(date -Iseconds)",
  "message": "Some tests failed"
}
EOF
    echo ""
    echo "================================================================================"
    echo "✗ SOME TESTS FAILED"
    echo "================================================================================"
    exit 1
fi
