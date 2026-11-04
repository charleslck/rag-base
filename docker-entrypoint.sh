#!/bin/sh
# RAGbase container entrypoint.
#
# 1. Generates a persistent JWT secret on first boot, so a plain
#    `docker compose up` is secure-by-default and sessions survive restarts.
# 2. Fixes ownership of the data volume, then drops root privileges.
set -eu

DATA_DIR="${RAGBASE_DATA_DIR:-/app/data}"
SECRET_FILE="${DATA_DIR}/.jwt_secret"

needs_secret=0
if [ -z "${RAGBASE_JWT_SECRET:-}" ] || [ "${RAGBASE_JWT_SECRET}" = "change-me-in-production" ]; then
    needs_secret=1
fi

if [ "${needs_secret}" = "1" ]; then
    mkdir -p "${DATA_DIR}"
    if [ ! -s "${SECRET_FILE}" ]; then
        python -c "import secrets; print(secrets.token_urlsafe(48))" > "${SECRET_FILE}"
        echo "[entrypoint] generated a new JWT secret at ${SECRET_FILE}" >&2
    fi
    RAGBASE_JWT_SECRET="$(cat "${SECRET_FILE}")"
    export RAGBASE_JWT_SECRET
fi

if [ "$(id -u)" = "0" ]; then
    mkdir -p "${DATA_DIR}"
    chown -R ragbase:ragbase "${DATA_DIR}" 2>/dev/null || true
    exec gosu ragbase "$@"
fi

exec "$@"
