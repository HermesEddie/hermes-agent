#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${ROOT_DIR:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
DOCKER_BIN="${DOCKER_BIN:-docker}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

DEFAULT_RELEASES_ROOT="${DEFAULT_RELEASES_ROOT:-${ROOT_DIR}/.artifacts/releases}"
DEFAULT_SRV_ROOT="${DEFAULT_SRV_ROOT:-/srv/hermes-agent}"
DEFAULT_ETC_ROOT="${DEFAULT_ETC_ROOT:-/etc/hermes-agent}"
DEFAULT_ENV_NAME="${DEFAULT_ENV_NAME:-production}"
DEFAULT_PLATFORM="${DEFAULT_PLATFORM:-linux/amd64}"
DEFAULT_TARGET="${DEFAULT_TARGET:-runtime-core}"
DEFAULT_NETWORK_NAME="${DEFAULT_NETWORK_NAME:-tower-aps-network-production}"
DEFAULT_RUNTIME_MODEL="${DEFAULT_RUNTIME_MODEL:-gpt-5.3-codex}"
DEFAULT_HEALTH_URL="${DEFAULT_HEALTH_URL:-http://127.0.0.1:8642/health}"

log() {
  printf '[hermes-ops] %s\n' "$*"
}

die() {
  printf '[hermes-ops] ERROR: %s\n' "$*" >&2
  exit 1
}

require_file() {
  local target="$1"
  [[ -f "${target}" ]] || die "missing file: ${target}"
}

ensure_dir() {
  local target="$1"
  mkdir -p "${target}"
}

git_sha() {
  git -C "${ROOT_DIR}" rev-parse HEAD
}

git_short_sha() {
  git -C "${ROOT_DIR}" rev-parse --short HEAD
}

release_id_default() {
  local short_sha
  short_sha="$(git_short_sha)"
  date -u +"%Y%m%dT%H%M%SZ-${short_sha}"
}

release_image_tag() {
  local release_id="$1"
  printf 'hermes-agent:release-%s' "${release_id}"
}

manifest_value() {
  local manifest_path="$1"
  local key="$2"
  "${PYTHON_BIN}" - <<'PY' "${manifest_path}" "${key}"
from __future__ import annotations

import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
key = sys.argv[2]
data = json.loads(manifest_path.read_text(encoding="utf-8"))
value = data.get(key)
if value is None:
    raise SystemExit(1)
print(value)
PY
}
