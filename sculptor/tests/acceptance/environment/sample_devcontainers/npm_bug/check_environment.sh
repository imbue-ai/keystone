#!/bin/sh

set -e

echo "=== NPM Environment Check ==="
echo "- Node.js:"
node --version

echo "- NPM:"
npm --version

echo "- NPM configuration:"
npm config list | sed 's/^/  /'

echo "✅ NPM environment checks passed!"
