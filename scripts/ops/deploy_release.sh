#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/common.sh
source "${SCRIPT_DIR}/common.sh"

RELEASE_DIR=""
RELEASE_ID=""
ENV_NAME="${DEFAULT_ENV_NAME}"
SRV_ROOT="${DEFAULT_SRV_ROOT}"
ETC_ROOT="${DEFAULT_ETC_ROOT}"
START=0

usage() {
  cat <<'USAGE'
Usage:
  scripts/ops/deploy_release.sh [options]

Options:
  --release-dir <path>    Release directory created by create_release.sh
  --release-id <id>       Release id under /srv/hermes-agent/releases
  --env <name>            Environment name (default: production)
  --srv-root <path>       Service root prefix
  --etc-root <path>       Config root prefix
  --start                 Start/recreate hermes-agent after loading the image
  -h, --help              Show this help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --release-dir)
      RELEASE_DIR="${2:?missing value for --release-dir}"
      shift 2
      ;;
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
    --etc-root)
      ETC_ROOT="${2:?missing value for --etc-root}"
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

if [[ -z "${RELEASE_DIR}" ]]; then
  [[ -n "${RELEASE_ID}" ]] || die "provide --release-dir or --release-id"
  RELEASE_DIR="${SRV_ROOT}/releases/${RELEASE_ID}"
fi

require_file "${RELEASE_DIR}/release-manifest.json"
require_file "${RELEASE_DIR}/hermes-image.tar.gz"

bootstrap_args=(
  --env "${ENV_NAME}"
  --srv-root "${SRV_ROOT}"
  --etc-root "${ETC_ROOT}"
)

"${SCRIPT_DIR}/bootstrap_runtime.sh" "${bootstrap_args[@]}"

release_id="$(manifest_value "${RELEASE_DIR}/release-manifest.json" release_id)"
image_tag="$(manifest_value "${RELEASE_DIR}/release-manifest.json" image_tag)"
runtime_root="${SRV_ROOT}/${ENV_NAME}"
current_link="${runtime_root}/current"
compose_file="${runtime_root}/compose.yaml"

ensure_dir "${SRV_ROOT}/releases/${release_id}"

if [[ "${RELEASE_DIR}" != "${SRV_ROOT}/releases/${release_id}" ]]; then
  cp -f "${RELEASE_DIR}/release-manifest.json" "${SRV_ROOT}/releases/${release_id}/release-manifest.json"
  cp -f "${RELEASE_DIR}/hermes-image.tar.gz" "${SRV_ROOT}/releases/${release_id}/hermes-image.tar.gz"
fi

"${DOCKER_BIN}" load -i "${SRV_ROOT}/releases/${release_id}/hermes-image.tar.gz"
ln -sfn "${SRV_ROOT}/releases/${release_id}" "${current_link}"

if [[ "${START}" == "1" ]]; then
  HERMES_AGENT_IMAGE="${image_tag}" \
    "${DOCKER_BIN}" compose -f "${compose_file}" up -d --force-recreate hermes-agent
fi

log "deployed release"
log "  release_id: ${release_id}"
log "  image_tag: ${image_tag}"
log "  current: ${current_link}"
