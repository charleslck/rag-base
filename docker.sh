#!/usr/bin/env bash
# RAGbase — one-command Docker launcher.
#
#   ./docker.sh              build + start, then wait until healthy
#   ./docker.sh logs         follow logs
#   ./docker.sh restart      rebuild + restart (data is kept)
#   ./docker.sh stop         stop the container (data is kept)
#   ./docker.sh down         remove container + network (data volume is kept)
#   ./docker.sh destroy      remove everything, including the data volume
#   ./docker.sh status       container + health status
#   ./docker.sh reset-password <email> <new-password>
set -euo pipefail
cd "$(dirname "$0")"

PORT="${RAGBASE_PORT:-8000}"
CONTAINER="ragbase"

if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
else
    echo "Docker Compose not found. Install Docker Desktop or the compose plugin." >&2
    exit 1
fi

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example (demo mode: no API key needed)."
fi

wait_for_health() {
    printf 'Waiting for RAGbase to become healthy'
    for _ in $(seq 1 60); do
        state=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}running{{end}}' "$CONTAINER" 2>/dev/null || echo missing)
        case "$state" in
            healthy) echo; return 0 ;;
            unhealthy)
                echo
                echo "Container reported unhealthy. Recent logs:" >&2
                "${COMPOSE[@]}" logs --tail 40 >&2
                return 1 ;;
            missing) echo; echo "Container is not running." >&2; return 1 ;;
        esac
        printf '.'
        sleep 1
    done
    echo
    echo "Timed out waiting for health. Check: ./docker.sh logs" >&2
    return 1
}

case "${1:-up}" in
    up|start)
        "${COMPOSE[@]}" up -d --build
        wait_for_health
        echo
        echo "RAGbase is running:  http://localhost:${PORT}"
        echo "First account you register becomes the admin."
        ;;
    restart)
        "${COMPOSE[@]}" up -d --build --force-recreate
        wait_for_health
        echo "Restarted: http://localhost:${PORT}"
        ;;
    logs)    "${COMPOSE[@]}" logs -f --tail 100 ;;
    stop)    "${COMPOSE[@]}" stop && echo "Stopped. Data preserved." ;;
    down)    "${COMPOSE[@]}" down && echo "Container removed. Data volume preserved." ;;
    destroy)
        read -r -p "This deletes the database and all uploaded documents. Continue? [y/N] " reply
        case "$reply" in
            [yY]*) "${COMPOSE[@]}" down -v && echo "Everything removed." ;;
            *)     echo "Aborted." ;;
        esac
        ;;
    status)
        docker ps -a --filter "name=${CONTAINER}" \
            --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
        ;;
    reset-password)
        shift
        if [ $# -lt 2 ]; then
            echo "Usage: ./docker.sh reset-password <email> <new-password>" >&2
            exit 1
        fi
        "${COMPOSE[@]}" exec ragbase python -m app.tools reset-password "$1" "$2"
        ;;
    *)
        echo "Usage: ./docker.sh [up|logs|restart|stop|down|destroy|status|reset-password]" >&2
        exit 1
        ;;
esac
