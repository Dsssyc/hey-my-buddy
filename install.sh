#!/bin/sh
# Bootstrap a fixed uv release, then hand installation to the published package.
# Usage: sh install.sh --version 0.19.0 [--wheel-url https://.../hey_my_buddy-0.19.0-py3-none-any.whl]
set -eu

UV_VERSION=0.12.19
UV_SUMS_SHA256=580e9742bc1ca4f9a6da3c4aa3db2dcceb0d0be84d75647abdae91bff68e52fc
PACKAGE_VERSION=0.19.0
WHEEL_URL=
fail() { printf '%s: %s Repair: %s\n' "$1" "$2" "$3" >&2; exit 1; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --version) [ "$#" -ge 2 ] || fail BOOTSTRAP_ARGS "Missing version." "Pass --version X.Y.Z."; PACKAGE_VERSION=$2; shift 2 ;;
    --wheel-url) [ "$#" -ge 2 ] || fail BOOTSTRAP_ARGS "Missing wheel URL." "Pass a fixed HTTPS wheel URL."; WHEEL_URL=$2; shift 2 ;;
    *) fail BOOTSTRAP_ARGS "Unknown option: $1." "Use --version X.Y.Z [--wheel-url URL]." ;;
  esac
done
case "$PACKAGE_VERSION" in *[!0-9A-Za-z.+_-]*|'') fail BOOTSTRAP_ARGS "Invalid version." "Pass a fixed version such as 0.19.0." ;; esac
case "$WHEEL_URL" in ''|https://*.whl) : ;; *) fail BOOTSTRAP_ARGS "Invalid wheel URL." "Use an HTTPS .whl URL or omit --wheel-url for PyPI." ;; esac
if [ -n "$WHEEL_URL" ]; then
  case "$WHEEL_URL" in */hey_my_buddy-"$PACKAGE_VERSION"-*.whl) : ;; *) fail BOOTSTRAP_ARGS "Wheel version or name does not match." "Use a hey_my_buddy wheel for --version $PACKAGE_VERSION." ;; esac
fi

UV_PRIVATE_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/hey-my-buddy/uv/$UV_VERSION"
UV_BIN="$UV_PRIVATE_DIR/uv"
printf 'Private uv destination (if needed): %s\n' "$UV_PRIVATE_DIR"
if command -v uv >/dev/null 2>&1; then
  UV_BIN=$(command -v uv)
elif [ ! -x "$UV_BIN" ]; then
  command -v curl >/dev/null 2>&1 || fail BOOTSTRAP_FETCH "curl is unavailable." "Install curl or provide uv on PATH."
  os=$(uname -s); arch=$(uname -m)
  case "$os:$arch" in
    Darwin:arm64) platform=aarch64-apple-darwin ;;
    Darwin:x86_64) platform=x86_64-apple-darwin ;;
    Linux:aarch64|Linux:arm64) platform=aarch64-unknown-linux-gnu ;;
    Linux:x86_64) platform=x86_64-unknown-linux-gnu ;;
    *) fail BOOTSTRAP_PLATFORM "Unsupported platform: $os/$arch." "Install uv manually and rerun this script." ;;
  esac
  archive="uv-$platform.tar.gz"
  base="https://releases.astral.sh/github/uv/releases/download/$UV_VERSION"
  temp=$(mktemp -d) || fail BOOTSTRAP_FETCH "Cannot create temporary directory." "Use a writable temporary directory."
  trap 'rm -rf "$temp"' EXIT HUP INT TERM
  curl --proto '=https' --tlsv1.2 -fsSL "$base/sha256.sum" -o "$temp/sha256.sum" || fail BOOTSTRAP_FETCH "Cannot download uv checksums." "Check network access to releases.astral.sh and retry."
  if command -v sha256sum >/dev/null 2>&1; then
    sum=$(sha256sum "$temp/sha256.sum" | cut -d ' ' -f 1)
  elif command -v shasum >/dev/null 2>&1; then
    sum=$(shasum -a 256 "$temp/sha256.sum" | cut -d ' ' -f 1)
  else
    fail BOOTSTRAP_VERIFY "No SHA-256 command." "Install sha256sum or shasum and retry."
  fi
  [ "$sum" = "$UV_SUMS_SHA256" ] || fail BOOTSTRAP_VERIFY "uv checksum list does not match the pinned release." "Get a trusted copy of uv and retry."
  expected=$(awk -v name="$archive" '$2 == name || $2 == "*" name {print $1}' "$temp/sha256.sum")
  [ -n "$expected" ] || fail BOOTSTRAP_VERIFY "No checksum for $archive." "Use a supported platform or install uv manually."
  curl --proto '=https' --tlsv1.2 -fsSL "$base/$archive" -o "$temp/$archive" || fail BOOTSTRAP_FETCH "Cannot download uv archive." "Check network access and retry."
  if command -v sha256sum >/dev/null 2>&1; then actual=$(sha256sum "$temp/$archive" | cut -d ' ' -f 1); else actual=$(shasum -a 256 "$temp/$archive" | cut -d ' ' -f 1); fi
  [ "$actual" = "$expected" ] || fail BOOTSTRAP_VERIFY "uv archive checksum mismatch." "Discard the download and retry from the official release."
  mkdir -p "$UV_PRIVATE_DIR" || fail BOOTSTRAP_WRITE "Cannot create private uv directory." "Allow writes to $UV_PRIVATE_DIR."
  tar -xzf "$temp/$archive" -C "$temp" || fail BOOTSTRAP_EXTRACT "Cannot extract uv archive." "Install tar and retry."
  [ -f "$temp/uv-$platform/uv" ] || fail BOOTSTRAP_EXTRACT "uv executable missing." "Check the official archive and retry."
  cp "$temp/uv-$platform/uv" "$UV_BIN.new" && chmod 755 "$UV_BIN.new" && mv "$UV_BIN.new" "$UV_BIN" || fail BOOTSTRAP_WRITE "Cannot place private uv." "Allow writes to $UV_PRIVATE_DIR."
fi

if [ -n "$WHEEL_URL" ]; then source=$WHEEL_URL; else source="hey-my-buddy==$PACKAGE_VERSION"; fi
printf 'Package source: %s\nuv: %s\nWrites: %s; ~/.agents/skills/buddy; ~/.claude/skills/buddy; ~/.local/share/hey-my-buddy\n' "$source" "$UV_BIN" "$UV_PRIVATE_DIR"
UV_BIN="$UV_BIN" "$UV_BIN" tool run --from "$source" hey-my-buddy install || fail PACKAGE_INSTALL_FAILED "Package installation failed." "Read the installer error above, correct it, and rerun this command."
