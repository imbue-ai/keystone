#!/bin/sh

set -e

cat /empty_amazonlinux.txt

echo "=== Amazon Linux Environment Check ==="
echo "- Amazon Linux version:"
cat /etc/os-release | grep PRETTY_NAME || echo "Could not determine Amazon Linux version"

echo "- Basic tools:"
which sh && echo "sh available"
which ls && ls --version | head -1 || echo "ls available"

echo "✅ Amazon Linux environment checks passed!"
