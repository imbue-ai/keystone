#!/bin/bash
# Generate JUnit XML test report fixtures from sample projects.
# Run this script to update the fixture files when sample projects change.
#
# Prerequisites:
#   brew install go rust node
#   go install github.com/jstemmer/go-junit-report/v2@latest
#   cargo install cargo-nextest --locked
#
# Usage: ./generate_fixtures.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
SAMPLES_DIR="$REPO_ROOT/samples"
OUTPUT_DIR="$SCRIPT_DIR"

echo "Generating JUnit XML test report fixtures..."
echo "Samples dir: $SAMPLES_DIR"
echo "Output dir: $OUTPUT_DIR"

# Ensure output directory exists
mkdir -p "$OUTPUT_DIR"

# --- Pytest reports ---
echo ""
echo "=== Generating pytest JUnit XML reports ==="

# Passing tests
cd "$SAMPLES_DIR/python_project"
PYTHONPATH=. uv run --isolated pytest --junitxml="$OUTPUT_DIR/pytest-passing.xml" tests/ -q || true
echo "Generated pytest-passing.xml"

# Failing tests
cd "$SAMPLES_DIR/python_with_failing_test"
PYTHONPATH=. uv run --isolated pytest --junitxml="$OUTPUT_DIR/pytest-failing.xml" tests/ -q || true
echo "Generated pytest-failing.xml"

# --- Go test reports ---
echo ""
echo "=== Generating Go JUnit XML reports ==="

if command -v go &> /dev/null; then
    GO_JUNIT_REPORT="${HOME}/go/bin/go-junit-report"
    if [ ! -x "$GO_JUNIT_REPORT" ]; then
        echo "Installing go-junit-report..."
        go install github.com/jstemmer/go-junit-report/v2@latest
    fi
    cd "$SAMPLES_DIR/go_project"
    go test -v ./... 2>&1 | "$GO_JUNIT_REPORT" > "$OUTPUT_DIR/go-passing.xml" || true
    echo "Generated go-passing.xml"
else
    echo "SKIP: Go not installed (brew install go)"
fi

# --- Node.js test reports ---
echo ""
echo "=== Generating Node.js JUnit XML reports ==="

if command -v node &> /dev/null; then
    cd "$SAMPLES_DIR/node_project"
    node --test --test-reporter=junit app.test.js > "$OUTPUT_DIR/node-passing.xml" 2>&1 || true
    echo "Generated node-passing.xml"
else
    echo "SKIP: Node not installed (brew install node)"
fi

# --- Cargo test reports ---
echo ""
echo "=== Generating Cargo JUnit XML reports ==="

if command -v cargo &> /dev/null; then
    if ! cargo nextest --version &> /dev/null; then
        echo "Installing cargo-nextest..."
        cargo install cargo-nextest --locked
    fi
    cd "$SAMPLES_DIR/rust_project"
    # Ensure nextest config exists for JUnit output
    mkdir -p .config
    cat > .config/nextest.toml << 'NEXTEST_EOF'
[profile.default]
junit.path = "junit.xml"
NEXTEST_EOF
    cargo nextest run 2>&1 || true
    if [ -f "target/nextest/default/junit.xml" ]; then
        cp target/nextest/default/junit.xml "$OUTPUT_DIR/cargo-passing.xml"
        echo "Generated cargo-passing.xml"
    else
        echo "SKIP: cargo-nextest didn't produce junit.xml"
    fi
else
    echo "SKIP: Cargo not installed (brew install rust)"
fi

echo ""
echo "=== Done ==="
ls -la "$OUTPUT_DIR"/*.xml 2>/dev/null || true
