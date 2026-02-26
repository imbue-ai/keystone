#!/bin/bash
# guardrail.sh — Agent self-check tool for validating devcontainer work.
#
# FIXME: This script should copy the .devcontainer directory onto a clean copy of the project and devcontainer build from there, to make sure the agent didn't change anything else.  This probably means we want to write a clean copy of the repo somewhere inside the image before starting the agent.
#
# Run this script from the project root to get structured feedback about
# common mistakes *before* the final verification step. It checks:
#   1. Required files exist (.devcontainer/devcontainer.json, Dockerfile, run_all_tests.sh)
#   2. Dockerfile basic structure (FROM, test_artifacts, COPY run_all_tests.sh)
#   3. run_all_tests.sh basic structure (JUnit output, final_result.json)
#   4. Docker image builds successfully
# FIXME: Add step 5: run the run_all_tests.sh script and check that everything passes and it produces junit xml in the right place.

#
# Exit code 0 = all checks pass, non-zero = at least one check failed.
# Output is structured feedback the agent can act on.

set -uo pipefail

ERRORS=0
WARNINGS=0

pass() {
    echo "  PASS: $1"
}

fail() {
    echo "  FAIL: $1"
    ERRORS=$((ERRORS + 1))
}

warn() {
    echo "  WARN: $1"
    WARNINGS=$((WARNINGS + 1))
}

echo "========================================"
echo "GUARDRAIL CHECK — Validating your work"
echo "========================================"
echo ""

# ------------------------------------------------------------------
# 1. Required files exist
# ------------------------------------------------------------------
echo "[1/4] Checking required files..."

if [ -d ".devcontainer" ]; then
    pass ".devcontainer/ directory exists"
else
    fail ".devcontainer/ directory is MISSING. Create it with: mkdir -p .devcontainer"
fi

if [ -f ".devcontainer/devcontainer.json" ]; then
    pass ".devcontainer/devcontainer.json exists"
else
    fail ".devcontainer/devcontainer.json is MISSING. Copy the pre-generated one: cp ./devcontainer.json .devcontainer/devcontainer.json"
fi

if [ -f ".devcontainer/Dockerfile" ]; then
    pass ".devcontainer/Dockerfile exists"
else
    fail ".devcontainer/Dockerfile is MISSING. You must create a Dockerfile inside .devcontainer/"
fi

if [ -f ".devcontainer/run_all_tests.sh" ]; then
    pass ".devcontainer/run_all_tests.sh exists"
    if [ -x ".devcontainer/run_all_tests.sh" ]; then
        pass ".devcontainer/run_all_tests.sh is executable"
    else
        fail ".devcontainer/run_all_tests.sh is NOT executable. Run: chmod +x .devcontainer/run_all_tests.sh"
    fi
else
    fail ".devcontainer/run_all_tests.sh is MISSING. You must create a test runner script."
fi

echo ""

# ------------------------------------------------------------------
# 2. Dockerfile structure checks
# ------------------------------------------------------------------
# FIXME: If we actually run the run all tests script from a built Docker image, we don't need all of these tests. 
echo "[2/4] Checking Dockerfile structure..."

if [ -f ".devcontainer/Dockerfile" ]; then
    # Check for FROM instruction
    if grep -qiE '^FROM ' .devcontainer/Dockerfile; then
        pass "Dockerfile has a FROM instruction"
    else
        fail "Dockerfile is missing a FROM instruction. Every Dockerfile must start with FROM."
    fi

    # Check for test_artifacts directory creation
    if grep -q 'test_artifacts' .devcontainer/Dockerfile; then
        pass "Dockerfile references test_artifacts directory"
    else
        fail "Dockerfile does not create /test_artifacts directory. Add: RUN mkdir -p /test_artifacts && chmod 777 /test_artifacts"
    fi

    # Check for COPY run_all_tests.sh (should be near end)
    if grep -qE 'COPY.*run_all_tests\.sh' .devcontainer/Dockerfile; then
        pass "Dockerfile copies run_all_tests.sh"
    else
        fail "Dockerfile does not COPY run_all_tests.sh. Add at the end: COPY .devcontainer/run_all_tests.sh /run_all_tests.sh"
    fi

    # Check for WORKDIR
    if grep -qE '^WORKDIR ' .devcontainer/Dockerfile; then
        pass "Dockerfile sets WORKDIR"
    else
        warn "Dockerfile does not set WORKDIR. Consider adding WORKDIR /project_src or similar."
    fi

    # Check for 'COPY . .' anti-pattern (should use explicit copies)
    if grep -qE '^COPY \. \.' .devcontainer/Dockerfile; then
        warn "Dockerfile uses 'COPY . .' which copies everything including .devcontainer/. Use explicit COPY commands for source files instead."
    fi
else
    echo "  (skipped — no Dockerfile)"
fi

echo ""

# ------------------------------------------------------------------
# 3. run_all_tests.sh structure checks
# ------------------------------------------------------------------
echo "[3/4] Checking run_all_tests.sh structure..."

if [ -f ".devcontainer/run_all_tests.sh" ]; then
    # Check for shebang
    if head -1 .devcontainer/run_all_tests.sh | grep -q '^#!/'; then
        pass "run_all_tests.sh has a shebang line"
    else
        fail "run_all_tests.sh is missing a shebang (e.g., #!/bin/bash). Add one at the top."
    fi

    # Check for JUnit XML output
    if grep -q 'junit' .devcontainer/run_all_tests.sh; then
        pass "run_all_tests.sh references junit output"
    else
        fail "run_all_tests.sh does not reference junit XML. Tests must produce JUnit XML in /test_artifacts/junit/*.xml"
    fi

    # Check for final_result.json
    if grep -q 'final_result.json' .devcontainer/run_all_tests.sh; then
        pass "run_all_tests.sh writes final_result.json"
    else
        fail "run_all_tests.sh does not write final_result.json. Must write to /test_artifacts/final_result.json"
    fi

    # Check for test_artifacts directory
    if grep -q 'test_artifacts' .devcontainer/run_all_tests.sh; then
        pass "run_all_tests.sh uses /test_artifacts directory"
    else
        fail "run_all_tests.sh does not reference /test_artifacts. All test artifacts must go to /test_artifacts/"
    fi
else
    echo "  (skipped — no run_all_tests.sh)"
fi

echo ""

# ------------------------------------------------------------------
# 4. Docker build check
# ------------------------------------------------------------------
echo "[4/4] Attempting Docker build..."

if [ -f ".devcontainer/Dockerfile" ] && [ -f ".devcontainer/devcontainer.json" ]; then
    IMAGE_NAME="guardrail-check-$(date +%s)"
    BUILD_OUTPUT=$(devcontainer build \
        --image-name "$IMAGE_NAME" \
        --workspace-folder . 2>&1)
    BUILD_EXIT=$?

    if [ $BUILD_EXIT -eq 0 ]; then
        pass "Docker image built successfully"
        # Clean up the test image
        docker rmi "$IMAGE_NAME" >/dev/null 2>&1 || true
    else
        fail "Docker build FAILED (exit code $BUILD_EXIT). Build output:"
        echo "--- BUILD OUTPUT START ---"
        echo "$BUILD_OUTPUT" | tail -50
        echo "--- BUILD OUTPUT END ---"
        echo ""
        echo "  Hints:"
        echo "  - Check that all COPY source paths exist relative to the project root"
        echo "  - Check that all package names in apt-get/pip/npm install are correct"
        echo "  - Check that the base image in FROM is valid and accessible"
    fi
else
    echo "  (skipped — missing Dockerfile or devcontainer.json)"
fi

echo ""

# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
echo "========================================"
if [ $ERRORS -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo "ALL CHECKS PASSED"
    echo "========================================"
    exit 0
elif [ $ERRORS -eq 0 ]; then
    echo "PASSED with $WARNINGS warning(s)"
    echo "========================================"
    exit 0
else
    echo "FAILED: $ERRORS error(s), $WARNINGS warning(s)"
    echo "Fix the errors above and run this script again."
    echo "========================================"
    exit 1
fi
