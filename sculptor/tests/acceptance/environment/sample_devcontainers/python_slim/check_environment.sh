#!/bin/sh

set -e

echo "=== Python Environment Check ==="
echo "- Python:"
python3 --version

echo "- pip:"
pip --version

echo "- Python environment details:"
which python3 | sed 's/^/  /'
python3 -c "import sys; print('  Python path: ' + sys.executable)"

echo "✅ Python environment checks passed!"
