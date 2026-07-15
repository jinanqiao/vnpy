#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"
COMPOSE_FILE="$ROOT_DIR/infra/postgres/compose.yaml"

if [[ ! -f "$ENV_FILE" ]]; then
  if command -v openssl >/dev/null 2>&1; then
    PASSWORD="$(openssl rand -base64 24 | tr -d '\n')"
  else
    PASSWORD="change-me-$(date +%s)"
  fi
  cat > "$ENV_FILE" <<EOF
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_DB=vnpy_quant
POSTGRES_USER=vnpy
POSTGRES_PASSWORD=$PASSWORD
POSTGRES_CONTAINER=vnpy-timescaledb
EOF
  chmod 600 "$ENV_FILE"
  echo "已生成本地数据库配置: $ENV_FILE"
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

case "${1:-up}" in
  up)
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d
    ;;
  down)
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" down
    ;;
  logs)
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" logs -f timescaledb
    ;;
  psql)
    docker exec -it "${POSTGRES_CONTAINER:-vnpy-timescaledb}" \
      psql -U "${POSTGRES_USER:-vnpy}" -d "${POSTGRES_DB:-vnpy_quant}"
    ;;
  check)
    docker exec "${POSTGRES_CONTAINER:-vnpy-timescaledb}" \
      psql -U "${POSTGRES_USER:-vnpy}" -d "${POSTGRES_DB:-vnpy_quant}" \
      -v ON_ERROR_STOP=1 \
      -c "SELECT extname, extversion FROM pg_extension WHERE extname IN ('timescaledb', 'pgcrypto') ORDER BY extname;" \
      -c "SELECT schemaname, tablename FROM pg_tables WHERE schemaname IN ('market', 'ops', 'live', 'risk') ORDER BY schemaname, tablename;"
    ;;
  *)
    echo "用法: $0 {up|down|logs|psql|check}" >&2
    exit 2
    ;;
esac
