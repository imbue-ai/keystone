#!/bin/sh

set -e

cat /empty_alpine.txt

echo "=== Alpine Environment Check ==="
echo "- Alpine version:"
cat /etc/alpine-release

echo "- Basic tools:"
which sh && sh --help | head -1 || echo "sh available"
which ls && ls --version | head -1 || echo "ls available"

echo "✅ Alpine environment checks passed!"
