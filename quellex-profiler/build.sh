#!/usr/bin/env bash
# PyInstaller build helper for macOS and Linux.
# Produces: dist/quellex-profiler-<os>-<arch>
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Creating virtualenv..."
  python3 -m venv .venv
fi

echo "Installing dependencies..."
./.venv/bin/python -m pip install --upgrade pip >/dev/null
./.venv/bin/python -m pip install -e ".[build]" >/dev/null

OS_NAME="linux"
if [ "$(uname)" = "Darwin" ]; then
  OS_NAME="macos"
fi

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64) ARCH="x64" ;;
  arm64|aarch64) ARCH="arm64" ;;
esac

NAME="quellex-profiler-${OS_NAME}-${ARCH}"
echo "Running PyInstaller -> ${NAME}"

./.venv/bin/pyinstaller \
  --onefile \
  --name "${NAME}" \
  --add-data "quellex_profiler/ui:quellex_profiler/ui" \
  --hidden-import "quellex_profiler.detect.gpu_windows" \
  --hidden-import "quellex_profiler.detect.gpu_apple" \
  --hidden-import "quellex_profiler.detect.gpu_linux" \
  quellex_profiler/__main__.py

echo ""
echo "Build complete:"
ls -la dist/
