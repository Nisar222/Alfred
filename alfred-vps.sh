#!/bin/bash
# Alfred VPS Helper Script
# Usage: ./alfred-vps.sh {logs|status|restart|db|recordings|pull|deploy|verify}
set -euo pipefail

VPS_HOST="nisar@165.154.217.39"
VPS_PATH="~/apps/alfred"
DB_USER="jamal"
DB_NAME="jamal_dialler"

case "${1:-}" in
  logs)
    echo "Fetching logs..."
    ssh "$VPS_HOST" "cd $VPS_PATH && docker compose logs api --tail=100"
    ;;

  status)
    echo "Checking status..."
    ssh "$VPS_HOST" "cd $VPS_PATH && docker compose ps"
    ;;

  restart)
    echo "Restarting API..."
    ssh "$VPS_HOST" "cd $VPS_PATH && docker compose restart api"
    ;;

  db)
    echo "Database stats..."
    ssh "$VPS_HOST" "cd $VPS_PATH && docker compose exec -T db psql -U $DB_USER -d $DB_NAME -c \"
      SELECT
        (SELECT COUNT(*) FROM calls WHERE status='completed') as completed_calls,
        (SELECT COUNT(*) FROM recordings) as total_recordings,
        (SELECT COUNT(*) FROM calls WHERE status='completed' AND id IN (SELECT call_id FROM recordings)) as calls_with_recordings;
    \""
    ;;

  recordings)
    echo "Recent calls and recordings..."
    ssh "$VPS_HOST" "cd $VPS_PATH && docker compose exec -T db psql -U $DB_USER -d $DB_NAME -c \"
      SELECT
        c.id,
        c.phone,
        c.completed_at,
        CASE WHEN r.id IS NOT NULL THEN 'YES' ELSE 'NO' END as has_recording
      FROM calls c
      LEFT JOIN recordings r ON r.call_id = c.id
      WHERE c.status = 'completed'
      ORDER BY c.completed_at DESC
      LIMIT 10;
    \""
    ;;

  pull)
    echo "WARNING: 'pull' only updates source and restarts — it does NOT rebuild"
    echo "the image, so code changes will NOT take effect. Use 'deploy' instead."
    echo "(This gap is exactly what shipped a broken app.js on 2026-08-13.)"
    exit 1
    ;;

  deploy)
    # Pulls latest code, rebuilds the image on the VPS from that working
    # tree, restarts, and verifies. A CI build-and-push-to-GHCR job (pull a
    # pre-tested image instead of building here) was tried on 2026-08-14 but
    # disabled after failing on every run — this repo's Actions workflow
    # permissions don't currently allow package writes (needs a GitHub admin:
    # Settings -> Actions -> General -> Workflow permissions -> "Read and
    # write permissions"). Once that's fixed and .github/workflows/ci.yml's
    # build-and-push job is restored, switch this back to `docker compose
    # pull api` instead of `build api`.
    echo "Pulling latest code..."
    ssh "$VPS_HOST" "cd $VPS_PATH && git pull --ff-only"
    echo "Building image from VPS working tree..."
    ssh "$VPS_HOST" "cd $VPS_PATH && docker compose build api"
    echo "Restarting API with newly built image..."
    ssh "$VPS_HOST" "cd $VPS_PATH && docker compose up -d api"
    echo "Verifying..."
    "$0" verify
    ;;

  verify)
    # Post-deploy smoke check: confirm the served frontend has no unresolved
    # merge-conflict markers and the container reports healthy. This is the
    # last line of defense if CI is ever bypassed.
    echo "Checking served app.js for conflict markers..."
    ssh "$VPS_HOST" "curl -fsS http://127.0.0.1:8000/app.js" > /tmp/alfred_verify_app_js
    if grep -qE '^(<{7}|={7}|>{7}) ?' /tmp/alfred_verify_app_js; then
      echo "FAIL: served app.js contains unresolved merge-conflict markers."
      rm -f /tmp/alfred_verify_app_js
      exit 1
    fi
    rm -f /tmp/alfred_verify_app_js
    echo "OK: app.js is clean."
    echo "Checking container health..."
    ssh "$VPS_HOST" "cd $VPS_PATH && docker compose ps api" | grep -q "healthy" \
      && echo "OK: api container healthy." \
      || { echo "FAIL: api container is not healthy."; exit 1; }
    echo "Checking /health endpoint..."
    ssh "$VPS_HOST" "curl -fsS -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health" | grep -q "200" \
      && echo "OK: /health returned 200." \
      || { echo "FAIL: /health did not return 200."; exit 1; }
    echo "Deploy verified."
    ;;

  *)
    echo "Alfred VPS Helper"
    echo ""
    echo "Usage: $0 {command}"
    echo ""
    echo "Commands:"
    echo "  logs                - Show last 100 API log lines"
    echo "  status              - Show container status"
    echo "  restart             - Restart API container (no image change)"
    echo "  db                  - Show database stats"
    echo "  recordings          - Show recent calls with recording status"
    echo "  deploy              - Pull code, build image on VPS, restart, verify"
    echo "  verify              - Run post-deploy smoke checks only"
    echo ""
    echo "Examples:"
    echo "  $0 deploy"
    echo "  $0 verify"
    echo "  $0 recordings"
    ;;
esac
