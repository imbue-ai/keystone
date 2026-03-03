#!/usr/bin/env bash
set -euo pipefail

git subtree pull --prefix=keystone https://github.com/imbue-ai/keystone.git main --squash
