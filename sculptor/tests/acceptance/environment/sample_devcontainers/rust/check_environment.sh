#!/bin/sh

set -e

echo "=== Rust Environment Check ==="
echo "- Rust:"
rustc --version
cargo --version

# rustup prints information that is architecture specific and thus unstable between laptop and CI/CD builds.
which rustup


echo "✅ Rust environment checks passed!"
