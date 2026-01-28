#!/bin/sh

set -e

echo "=== Non-standard Build Context Environment Check ==="
echo "- Alpine version:"
cat /etc/alpine-release || echo "Could not determine Alpine version"

echo "- Basic tools:"
which sh && sh --help | head -1 || echo "sh available"
which ls

echo "- Checking for test file from build context:"
if [ -f /test_file.txt ]; then
    echo "✅ Test file exists at /test_file.txt"
    echo "Content:"
    cat /test_file.txt
else
    echo "❌ Test file not found at /test_file.txt"
    exit 1
fi
cat /test_file.txt

echo "✅ Non-standard build context environment checks passed!"
