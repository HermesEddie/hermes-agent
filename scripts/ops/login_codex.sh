#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/common.sh
source "${SCRIPT_DIR}/common.sh"

ENV_NAME="${DEFAULT_ENV_NAME}"
SRV_ROOT="${DEFAULT_SRV_ROOT}"
ETC_ROOT="${DEFAULT_ETC_ROOT}"
NETWORK_NAME="${DEFAULT_NETWORK_NAME}"
IMAGE="${HERMES_AGENT_IMAGE:-}"

usage() {
  cat <<'USAGE'
Usage:
  scripts/ops/login_codex.sh [options] <image>

Options:
  --env <name>            Environment name (default: production)
  --srv-root <path>       Service root prefix
  --etc-root <path>       Config root prefix
  --network <name>        Docker network name
  -h, --help              Show this help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
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
    --network)
      NETWORK_NAME="${2:?missing value for --network}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      IMAGE="${1}"
      shift
      ;;
  esac
done

[[ -n "${IMAGE}" ]] || die "missing image tag"

env_file="${ETC_ROOT}/${ENV_NAME}/hermes-agent.env"
home_dir="${SRV_ROOT}/${ENV_NAME}/home"

require_file "${env_file}"
[[ -d "${home_dir}" ]] || die "missing runtime home dir: ${home_dir}"

"${DOCKER_BIN}" run --rm -it \
  --network "${NETWORK_NAME}" \
  --env-file "${env_file}" \
  -e HERMES_HOME=/opt/data \
  -v "${home_dir}:/opt/data" \
  "${IMAGE}" \
  auth
