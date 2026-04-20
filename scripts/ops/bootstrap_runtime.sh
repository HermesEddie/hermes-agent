#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/common.sh
source "${SCRIPT_DIR}/common.sh"

ENV_NAME="${DEFAULT_ENV_NAME}"
SRV_ROOT="${DEFAULT_SRV_ROOT}"
ETC_ROOT="${DEFAULT_ETC_ROOT}"
NETWORK_NAME="${DEFAULT_NETWORK_NAME}"
RUNTIME_MODEL="${DEFAULT_RUNTIME_MODEL}"
FORCE=0

usage() {
  cat <<'USAGE'
Usage:
  scripts/ops/bootstrap_runtime.sh [options]

Options:
  --env <name>              Environment name (default: production)
  --srv-root <path>         Service root prefix (default: /srv/hermes-agent)
  --etc-root <path>         Config root prefix (default: /etc/hermes-agent)
  --network <name>          External docker network name
  --runtime-model <model>   Default TOWER_AGENT_REVIEW_RUNTIME_MODEL
  --force                   Overwrite existing files
  -h, --help                Show this help
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
    --runtime-model)
      RUNTIME_MODEL="${2:?missing value for --runtime-model}"
      shift 2
      ;;
    --force)
      FORCE=1
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
home_root="${runtime_root}/home"
etc_env_root="${ETC_ROOT}/${ENV_NAME}"
env_file="${etc_env_root}/hermes-agent.env"
compose_file="${runtime_root}/compose.yaml"
login_script="${runtime_root}/login_codex.sh"

ensure_dir "${home_root}"
ensure_dir "${etc_env_root}"

if [[ "${FORCE}" != "1" && -e "${env_file}" ]]; then
  die "env file exists: ${env_file} (pass --force to overwrite)"
fi
if [[ "${FORCE}" != "1" && -e "${compose_file}" ]]; then
  die "compose file exists: ${compose_file} (pass --force to overwrite)"
fi
if [[ "${FORCE}" != "1" && -e "${login_script}" ]]; then
  die "login script exists: ${login_script} (pass --force to overwrite)"
fi

cat > "${env_file}" <<EOF
# Hermes agent runtime for ${ENV_NAME}
API_SERVER_ENABLED=1
API_SERVER_HOST=0.0.0.0
API_SERVER_PORT=8642
API_SERVER_KEY=REPLACE_ME
SALES_TARGET_AGENT_INTERNAL_TOKEN=REPLACE_ME
TOWER_AGENT_REVIEW_RUNTIME_MODEL=${RUNTIME_MODEL}
API_SERVER_MODEL_NAME=hermes-agent
HERMES_HOME=/opt/data
EOF

cat > "${compose_file}" <<EOF
services:
  hermes-agent:
    image: \${HERMES_AGENT_IMAGE:?HERMES_AGENT_IMAGE is required}
    container_name: \${HERMES_AGENT_CONTAINER_NAME:-hermes-agent-${ENV_NAME}}
    env_file:
      - ${env_file}
    command: ["gateway", "run", "--replace"]
    ports:
      - "127.0.0.1:\${HERMES_AGENT_BIND_PORT:-8642}:8642"
    restart: unless-stopped
    volumes:
      - ${home_root}:/opt/data
    networks:
      tower_shared:
        aliases:
          - hermes-agent

networks:
  tower_shared:
    external: true
    name: ${NETWORK_NAME}
EOF

cat > "${login_script}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
IMAGE="\${1:-\${HERMES_AGENT_IMAGE:-}}"
if [[ -z "\${IMAGE}" ]]; then
  echo "Usage: \$0 <hermes-image>" >&2
  exit 1
fi

${DOCKER_BIN} run --rm -it \\
  --network ${NETWORK_NAME} \\
  --env-file ${env_file} \\
  -e HERMES_HOME=/opt/data \\
  -v ${home_root}:/opt/data \\
  "\${IMAGE}" \\
  auth
EOF

chmod 600 "${env_file}"
chmod 755 "${login_script}"

log "bootstrapped runtime layout"
log "  env: ${env_file}"
log "  compose: ${compose_file}"
log "  home: ${home_root}"
log "  login: ${login_script}"
