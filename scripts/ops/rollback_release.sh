#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/common.sh
source "${SCRIPT_DIR}/common.sh"

RELEASE_ID=""
ENV_NAME="${DEFAULT_ENV_NAME}"
SRV_ROOT="${DEFAULT_SRV_ROOT}"
START=0

usage() {
  cat <<'USAGE'
Usage:
  scripts/ops/rollback_release.sh --release-id <id> [options]

Options:
  --release-id <id>       Release id to restore
  --env <name>            Environment name (default: production)
  --srv-root <path>       Service root prefix
  --start                 Restart hermes-agent after switching current
  -h, --help              Show this help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --release-id)
      RELEASE_ID="${2:?missing value for --release-id}"
      shift 2
      ;;
    --env)
      ENV_NAME="${2:?missing value for --env}"
      shift 2
      ;;
    --srv-root)
      SRV_ROOT="${2:?missing value for --srv-root}"
      shift 2
      ;;
    --start)
      START=1
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

[[ -n "${RELEASE_ID}" ]] || die "--release-id is required"

release_dir="${SRV_ROOT}/releases/${RELEASE_ID}"
manifest_path="${release_dir}/release-manifest.json"
compose_file="${SRV_ROOT}/${ENV_NAME}/compose.yaml"
current_link="${SRV_ROOT}/${ENV_NAME}/current"

require_file "${manifest_path}"
image_tag="$(manifest_value "${manifest_path}" image_tag)"

ln -sfn "${release_dir}" "${current_link}"

if [[ "${START}" == "1" ]]; then
  HERMES_AGENT_IMAGE="${image_tag}" \
    "${DOCKER_BIN}" compose -f "${compose_file}" up -d --force-recreate hermes-agent
fi

log "rolled back release"
log "  release_id: ${RELEASE_ID}"
log "  image_tag: ${image_tag}"
