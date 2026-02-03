#!/bin/bash
set -euo pipefail

echo "Starting test suite for sample-python-project"
echo "================================================"

# Create JUnit XML directory
mkdir -p /test_artifacts/junit

# Set PYTHONPATH to include project source
export PYTHONPATH=/project_src:${PYTHONPATH:-}

# Run pytest with JUnit XML output and timestamping
echo "Running pytest..."
/timestamp_process_output.pl --logfile /test_artifacts/pytest.log \
  pytest --junitxml=/test_artifacts/junit/pytest.xml --verbose tests/

# Capture exit code
TEST_EXIT_CODE=$?

# Create final result JSON
if [ $TEST_EXIT_CODE -eq 0 ]; then
  echo '{"success": true, "message": "All tests passed"}' > /test_artifacts/final_result.json
  echo "================================================"
  echo "SUCCESS: All tests passed!"
else
  echo '{"success": false, "message": "Tests failed"}' > /test_artifacts/final_result.json
  echo "================================================"
  echo "FAILURE: Tests failed with exit code $TEST_EXIT_CODE"
fi

exit $TEST_EXIT_CODE
