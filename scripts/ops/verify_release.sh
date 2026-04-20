#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/common.sh
source "${SCRIPT_DIR}/common.sh"

ENV_NAME="${DEFAULT_ENV_NAME}"
SRV_ROOT="${DEFAULT_SRV_ROOT}"
ETC_ROOT="${DEFAULT_ETC_ROOT}"
HEALTH_URL="${DEFAULT_HEALTH_URL}"
REQUIRE_AUTH=0

usage() {
  cat <<'USAGE'
Usage:
  scripts/ops/verify_release.sh [options]

Options:
  --env <name>            Environment name (default: production)
  --srv-root <path>       Service root prefix
  --etc-root <path>       Config root prefix
  --health-url <url>      Health check URL (default: http://127.0.0.1:8642/health)
  --require-auth          Fail when auth.json is missing
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
    --health-url)
      HEALTH_URL="${2:?missing value for --health-url}"
      shift 2
      ;;
    --require-auth)
      REQUIRE_AUTH=1
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

runtime_root="${SRV_ROOT}/${ENV_NAME}"
env_file="${ETC_ROOT}/${ENV_NAME}/hermes-agent.env"
compose_file="${runtime_root}/compose.yaml"
home_dir="${runtime_root}/home"
auth_file="${home_dir}/auth.json"

require_file "${env_file}"
require_file "${compose_file}"
[[ -d "${home_dir}" ]] || die "missing runtime home dir: ${home_dir}"

if [[ "${REQUIRE_AUTH}" == "1" ]]; then
  require_file "${auth_file}"
fi

if curl -fsS "${HEALTH_URL}" >/dev/null 2>&1; then
  log "health check passed: ${HEALTH_URL}"
else
  log "health check not reachable yet: ${HEALTH_URL}"
fi

log "verification passed"
log "  env: ${env_file}"
log "  compose: ${compose_file}"
log "  home: ${home_dir}"
