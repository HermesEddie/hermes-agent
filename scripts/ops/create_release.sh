#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/common.sh
source "${SCRIPT_DIR}/common.sh"

RELEASE_ID=""
OUTPUT_ROOT="${DEFAULT_RELEASES_ROOT}"
PLATFORM="${DEFAULT_PLATFORM}"
TARGET="${DEFAULT_TARGET}"
DRY_RUN=0

usage() {
  cat <<'USAGE'
Usage:
  scripts/ops/create_release.sh [options]

Options:
  --release-id <id>       Explicit release id
  --output-root <path>    Output root (default: .artifacts/releases)
  --platform <platform>   Docker platform (default: linux/amd64)
  --target <target>       Docker target (default: runtime-core)
  --dry-run               Print resolved values without building
  -h, --help              Show this help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --release-id)
      RELEASE_ID="${2:?missing value for --release-id}"
      shift 2
      ;;
    --output-root)
      OUTPUT_ROOT="${2:?missing value for --output-root}"
      shift 2
      ;;
    --platform)
      PLATFORM="${2:?missing value for --platform}"
      shift 2
      ;;
    --target)
      TARGET="${2:?missing value for --target}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
done

if [[ -z "${RELEASE_ID}" ]]; then
  RELEASE_ID="$(release_id_default)"
fi

release_dir="${OUTPUT_ROOT}/${RELEASE_ID}"
image_tag="$(release_image_tag "${RELEASE_ID}")"
archive_path="${release_dir}/hermes-image.tar.gz"
manifest_path="${release_dir}/release-manifest.json"

if [[ "${DRY_RUN}" == "1" ]]; then
  cat <<EOF
release_id=${RELEASE_ID}
release_dir=${release_dir}
image_tag=${image_tag}
platform=${PLATFORM}
target=${TARGET}
archive_path=${archive_path}
manifest_path=${manifest_path}
git_sha=$(git_sha)
EOF
  exit 0
fi

ensure_dir "${release_dir}"

"${DOCKER_BIN}" build \
  --platform "${PLATFORM}" \
  --target "${TARGET}" \
  -t "${image_tag}" \
  -f "${ROOT_DIR}/Dockerfile.prod" \
  "${ROOT_DIR}"

"${DOCKER_BIN}" save "${image_tag}" | gzip > "${archive_path}"

"${PYTHON_BIN}" - <<'PY' "${manifest_path}" "${RELEASE_ID}" "${image_tag}" "${PLATFORM}" "${TARGET}" "${archive_path}" "$(git_sha)"
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
release_id = sys.argv[2]
image_tag = sys.argv[3]
platform = sys.argv[4]
target = sys.argv[5]
archive_path = Path(sys.argv[6])
git_sha = sys.argv[7]

sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()
manifest = {
    "schema_version": "1",
    "release_id": release_id,
    "git_sha": git_sha,
    "image_tag": image_tag,
    "platform": platform,
    "target": target,
    "created_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "artifacts": {
        "image_archive": archive_path.name,
    },
    "checksums": {
        archive_path.name: sha256,
    },
}
manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
PY

log "created release"
log "  release_id: ${RELEASE_ID}"
log "  release_dir: ${release_dir}"
log "  image_tag: ${image_tag}"
