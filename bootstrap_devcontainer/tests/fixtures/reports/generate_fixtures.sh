#!/bin/bash
# Generate test report fixtures from sample projects.
# Run this script to update the fixture files when report formats change.
#
# Prerequisites: brew install go rust node
#
# Usage: ./generate_fixtures.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
SAMPLES_DIR="$REPO_ROOT/samples"
OUTPUT_DIR="$SCRIPT_DIR"

echo "Generating test report fixtures..."
echo "Samples dir: $SAMPLES_DIR"
echo "Output dir: $OUTPUT_DIR"

# Ensure output directory exists
mkdir -p "$OUTPUT_DIR"

# --- Pytest reports ---
echo ""
echo "=== Generating pytest reports ==="

# Passing tests
cd "$SAMPLES_DIR/python_project"
PYTHONPATH=. uv run --with pytest-json-report --isolated pytest \
    --json-report --json-report-file="$OUTPUT_DIR/pytest-passing.json" \
    tests/ -q || true
echo "Generated pytest-passing.json"

# Failing tests
cd "$SAMPLES_DIR/python_with_failing_test"
PYTHONPATH=. uv run --with pytest-json-report --isolated pytest \
    --json-report --json-report-file="$OUTPUT_DIR/pytest-failing.json" \
    tests/ -q || true
echo "Generated pytest-failing.json"

# --- Go test reports ---
echo ""
echo "=== Generating Go test reports ==="

if command -v go &> /dev/null; then
    cd "$SAMPLES_DIR/go_project"
    go test -json ./... > "$OUTPUT_DIR/go-test-passing.json" 2>&1 || true
    echo "Generated go-test-passing.json"
else
    echo "SKIP: Go not installed (brew install go)"
fi

# --- Node.js test reports ---
echo ""
echo "=== Generating Node.js test reports ==="

if command -v node &> /dev/null; then
    cd "$SAMPLES_DIR/node_project"
    
    # TAP format (Node built-in)
    node --test --test-reporter=tap app.test.js > "$OUTPUT_DIR/node-tap.tap" 2>&1 || true
    echo "Generated node-tap.tap"
    
    # Jest format (if jest available)
    if [ -f package-lock.json ] || npm install --save-dev jest 2>/dev/null; then
        npx jest --json --outputFile="$OUTPUT_DIR/node-jest.json" app.test.js 2>/dev/null || true
        if [ -f "$OUTPUT_DIR/node-jest.json" ]; then
            echo "Generated node-jest.json"
        fi
    fi
    
    # Mocha format (if mocha available)
    if npm install --save-dev mocha 2>/dev/null; then
        npx mocha --reporter json app.test.js > "$OUTPUT_DIR/node-mocha.json" 2>/dev/null || true
        if [ -f "$OUTPUT_DIR/node-mocha.json" ] && [ -s "$OUTPUT_DIR/node-mocha.json" ]; then
            echo "Generated node-mocha.json"
        else
            rm -f "$OUTPUT_DIR/node-mocha.json"
        fi
    fi
    
    # Clean up node_modules
    rm -rf node_modules package-lock.json 2>/dev/null || true
else
    echo "SKIP: Node not installed (brew install node)"
fi

# --- Cargo test reports ---
echo ""
echo "=== Generating Cargo test reports ==="

if command -v cargo &> /dev/null; then
    cd "$SAMPLES_DIR/rust_project"
    # JSON format requires nightly or recent stable
    cargo test -- -Z unstable-options --format json > "$OUTPUT_DIR/cargo-test-passing.json" 2>&1 || true
    if [ -s "$OUTPUT_DIR/cargo-test-passing.json" ]; then
        echo "Generated cargo-test-passing.json"
    else
        echo "SKIP: Cargo JSON output not available (may need nightly)"
        rm -f "$OUTPUT_DIR/cargo-test-passing.json"
    fi
else
    echo "SKIP: Cargo not installed (brew install rust)"
fi

echo ""
echo "=== Done ==="
ls -la "$OUTPUT_DIR"/*.json "$OUTPUT_DIR"/*.tap 2>/dev/null || true
