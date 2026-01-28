#!/bin/sh

set -e

cat /empty_debian.txt

echo "=== Debian Environment Check ==="
echo "- Debian version:"
cat /etc/os-release | grep PRETTY_NAME || echo "Could not determine Debian version"

echo "- Basic tools:"
which sh && echo "sh available"
which ls && ls --version | head -1 || echo "ls available"

echo "✅ Debian environment checks passed!"
