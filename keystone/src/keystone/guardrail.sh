#!/bin/bash
# guardrail.sh — Agent self-check tool for validating devcontainer work.
#
# Run this script from the project root to get structured feedback about
# common mistakes *before* the final verification step. It checks:
#   1. Required files exist (.devcontainer/devcontainer.json, Dockerfile, run_all_tests.sh)
#   2. Docker image builds successfully (from a clean copy of the project)
#   3. Tests pass and produce JUnit XML + final_result.json
#
# Exit code 0 = all checks pass, non-zero = at least one check failed.
# Output is structured feedback the agent can act on.

set -uo pipefail

ERRORS=0
WARNINGS=0
BUILT_IMAGE=""

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
echo "[1/3] Checking required files..."

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
# 2. Docker build check
# ------------------------------------------------------------------
echo "[2/3] Attempting Docker build..."

if [ -f ".devcontainer/Dockerfile" ] && [ -f ".devcontainer/devcontainer.json" ]; then
    # Build from a clean copy of the project with only .devcontainer/ overlaid.
    # This verifies the agent didn't modify source files outside .devcontainer/.
    if [ -d "/project_clean" ]; then
        CLEAN_SRC="/project_clean"
    elif [ -d ".project_clean" ]; then
        CLEAN_SRC=".project_clean"
    else
        CLEAN_SRC=""
    fi

    if [ -z "$CLEAN_SRC" ]; then
        fail "No clean project copy found (expected /project_clean or .project_clean). Cannot verify build isolation."
    else
        BUILD_DIR=$(mktemp -d)
        cp -r "$CLEAN_SRC/." "$BUILD_DIR/"
        rm -rf "$BUILD_DIR/.devcontainer"
        cp -r .devcontainer/ "$BUILD_DIR/.devcontainer"

        IMAGE_NAME="guardrail-check-$(date +%s)"
        BUILD_OUTPUT=$(devcontainer build \
            --image-name "$IMAGE_NAME" \
            --workspace-folder "$BUILD_DIR" 2>&1)
        BUILD_EXIT=$?

        rm -rf "$BUILD_DIR"

        if [ $BUILD_EXIT -eq 0 ]; then
            pass "Docker image built successfully"
            BUILT_IMAGE="$IMAGE_NAME"
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
    fi
else
    fail "Dockerfile or devcontainer.json is missing — cannot attempt Docker build."
fi

echo ""

# ------------------------------------------------------------------
# 3. Test run check
# ------------------------------------------------------------------
echo "[3/3] Running tests..."

if [ -n "$BUILT_IMAGE" ]; then
    ARTIFACTS_DIR=$(mktemp -d)
    CONTAINER_NAME="guardrail-run-$(date +%s)"

    docker run --network=host --name "$CONTAINER_NAME" "$BUILT_IMAGE" /run_all_tests.sh
    RUN_EXIT=$?

    docker cp "$CONTAINER_NAME:/test_artifacts/." "$ARTIFACTS_DIR/" 2>/dev/null || true
    docker rm "$CONTAINER_NAME" >/dev/null 2>&1 || true
    docker rmi "$BUILT_IMAGE" >/dev/null 2>&1 || true

    if [ $RUN_EXIT -eq 0 ]; then
        pass "Tests passed (exit 0)"
    else
        fail "Tests FAILED (exit code $RUN_EXIT)"
    fi

    if ls "$ARTIFACTS_DIR/junit/"*.xml >/dev/null 2>&1; then
        pass "JUnit XML found in /test_artifacts/junit/"
    else
        fail "No JUnit XML found in /test_artifacts/junit/*.xml"
    fi

    if [ -f "$ARTIFACTS_DIR/final_result.json" ]; then
        pass "final_result.json found in /test_artifacts/"
    else
        fail "final_result.json not found in /test_artifacts/"
    fi

    rm -rf "$ARTIFACTS_DIR"
else
    fail "Docker image was not built — cannot run tests."
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
