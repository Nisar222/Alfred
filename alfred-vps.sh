#!/bin/bash
# Alfred VPS Helper Script
# Usage: ./alfred-vps.sh {logs|status|restart|db|recordings}

VPS_HOST="nisar@165.154.217.39"
VPS_PATH="~/apps/alfred"

case "$1" in
  logs)
    echo "Fetching logs..."
    ssh $VPS_HOST "cd $VPS_PATH && docker compose logs api --tail=100"
    ;;
  
  status)
    echo "Checking status..."
    ssh $VPS_HOST "cd $VPS_PATH && docker compose ps"
    ;;
  
  restart)
    echo "Restarting API..."
    ssh $VPS_HOST "cd $VPS_PATH && docker compose restart api"
    ;;
  
  db)
    echo "Database stats..."
    ssh $VPS_HOST "cd $VPS_PATH && docker compose exec -T db psql -U alfred -d alfred -c \"
      SELECT 
        (SELECT COUNT(*) FROM calls WHERE status='completed') as completed_calls,
        (SELECT COUNT(*) FROM recordings) as total_recordings,
        (SELECT COUNT(*) FROM calls WHERE status='completed' AND id IN (SELECT call_id FROM recordings)) as calls_with_recordings;
    \""
    ;;
  
  recordings)
    echo "Recent calls and recordings..."
    ssh $VPS_HOST "cd $VPS_PATH && docker compose exec -T db psql -U alfred -d alfred -c \"
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
    echo "Pulling latest code and restarting..."
    ssh $VPS_HOST "cd $VPS_PATH && git pull && docker compose restart api"
    ;;
  
  *)
    echo "Alfred VPS Helper"
    echo ""
    echo "Usage: $0 {command}"
    echo ""
    echo "Commands:"
    echo "  logs       - Show last 100 API log lines"
    echo "  status     - Show container status"
    echo "  restart    - Restart API container"
    echo "  db         - Show database stats"
    echo "  recordings - Show recent calls with recording status"
    echo "  pull       - Pull latest code and restart"
    echo ""
    echo "Examples:"
    echo "  $0 logs"
    echo "  $0 status"
    echo "  $0 recordings"
    ;;
esac
