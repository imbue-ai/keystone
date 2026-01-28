#!/bin/sh

set -e

echo "=== Go Environment Check ==="
echo "- Go:"
go version

echo "- Go environment:"
echo "  GOROOT: $GOROOT"
echo "  GOPATH: $GOPATH"
go env GOOS GOARCH | sed 's/^/  /'

echo "✅ Go environment checks passed!"
