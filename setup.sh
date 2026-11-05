#!/bin/bash
# retroarch-mac-setup — install and configure RetroArch on Apple Silicon.
# Idempotent: safe to run as many times as you want. See README.md.
set -euo pipefail

VOLUME="/Volumes/BACKUP"
ROMS="$VOLUME/roms"
CACHE="$VOLUME/retroarch-cache"
TOOLS="$CACHE/tools"
PY_DIR="$TOOLS/python"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bold() { printf '\033[1;36m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[31m✖ %s\033[0m\n' "$*" >&2; exit 1; }

# ------------------------------------------------------------------ preflight
[[ "$(uname -s)" == "Darwin" ]] || fail "This script is for macOS."
[[ "$(uname -m)" == "arm64" ]]  || fail "This script targets Apple Silicon (arm64)."

if ! mount | grep -q " on $VOLUME "; then
  fail "The external disk is not mounted at $VOLUME. Connect the BACKUP disk and run ./setup.sh again."
fi
[[ -d "$ROMS" ]] || fail "$ROMS does not exist. Nothing to do."
[[ -w "$VOLUME" ]] || fail "$VOLUME is not writable."
mkdir -p "$CACHE" "$TOOLS"

# ------------------------------------------------------------------ Python (portable copy kept on the disk)
# macOS' /usr/bin/python3 needs the Command Line Tools (internet). A standalone CPython from
# python-build-standalone is cached on the external disk so the script also works offline
# right after reinstalling macOS.
online() { curl -fsI --max-time 6 https://api.github.com >/dev/null 2>&1; }

install_portable_python() {
  bold "Caching a portable Python on the external disk ($PY_DIR)"
  local api="https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
  local json url sums name tmp
  json="$(curl -fsSL "$api")"
  url="$(printf '%s' "$json" | grep -oE '"browser_download_url": *"[^"]*cpython-3\.12\.[0-9]+(\+|%2B)[0-9]+-aarch64-apple-darwin-install_only\.tar\.gz"' | head -1 | sed -E 's/.*"(https[^"]+)"/\1/')"
  sums="$(printf '%s' "$json" | grep -oE '"browser_download_url": *"[^"]*SHA256SUMS"' | head -1 | sed -E 's/.*"(https[^"]+)"/\1/')"
  [[ -n "$url" ]] || { echo "    could not find a Python build to download"; return 1; }
  name="$(basename "$url")"; name="${name//%2B/+}"
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/$name" "$url"
  if [[ -n "$sums" ]]; then
    local expected
    expected="$(curl -fsSL "$sums" | grep " $name\$" | awk '{print $1}')"
    if [[ -n "$expected" ]] && [[ "$(shasum -a 256 "$tmp/$name" | awk '{print $1}')" != "$expected" ]]; then
      rm -rf "$tmp"; echo "    checksum mismatch"; return 1
    fi
  fi
  mkdir -p "$tmp/x" && tar -xzf "$tmp/$name" -C "$tmp/x"
  rm -rf "$PY_DIR" && mv "$tmp/x/python" "$PY_DIR"
  cp "$tmp/$name" "$TOOLS/" 2>/dev/null || true
  rm -rf "$tmp"
  echo "    ✔ $("$PY_DIR/bin/python3" --version)"
}

PY=""
if [[ -x "$PY_DIR/bin/python3" ]] && "$PY_DIR/bin/python3" -c 'import sys' 2>/dev/null; then
  PY="$PY_DIR/bin/python3"
elif online && install_portable_python; then
  PY="$PY_DIR/bin/python3"
elif xcode-select -p >/dev/null 2>&1 && /usr/bin/python3 -c 'import sys' 2>/dev/null; then
  PY="/usr/bin/python3"
else
  fail "No Python available: connect to the internet once (portable Python is cached on the disk)."
fi

exec "$PY" -B "$HERE/lib/main.py" "$@"
