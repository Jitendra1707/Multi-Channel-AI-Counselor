#!/usr/bin/env bash
# SoulX pool entrypoint — runs K single-stream worker processes behind one nginx.
#
# Each worker is the proven single-stream server (one pipeline ≈ 9 GB VRAM); the
# pool gives concurrency by REPLICATION (N processes on the one GPU), not by
# multiplexing one pipeline — so flash_head's non-re-entrant / globally-stateful
# internals are sidestepped entirely by process isolation.
#
#   nginx :8011 (public)  →  least_conn over workers, max_conns=1 each
#     worker 0 :8012   (PRIMARY — generates the shared idle/thinking .pkl caches)
#     worker 1 :8013
#     ...      :8012+i
#
# Tunables (env):
#   SOULX_POOL_SIZE         number of workers          (default 3)
#   SOULX_WORKER_BASE_PORT  first worker port          (default 8012)
#   SOULX_PUBLIC_PORT       nginx listen port          (default 8011)
set -euo pipefail

POOL_SIZE="${SOULX_POOL_SIZE:-3}"
BASE_PORT="${SOULX_WORKER_BASE_PORT:-8012}"
PUBLIC_PORT="${SOULX_PUBLIC_PORT:-8011}"
APP_DIR="${SOULX_APP_DIR:-/opt/SoulX-FlashHead}"

echo "[entrypoint] SoulX pool: ${POOL_SIZE} worker(s); nginx :${PUBLIC_PORT} -> workers :${BASE_PORT}.."

# ── Render nginx config ──────────────────────────────────────────────────────
WS_SERVERS=""
HEALTH_SERVERS=""
for i in $(seq 0 $((POOL_SIZE - 1))); do
  port=$((BASE_PORT + i))
  # max_conns=1: a worker holds exactly one live WebSocket; a (K+1)-th client gets
  # a 502 from nginx (no free worker) rather than corrupting an active stream.
  WS_SERVERS+="        server 127.0.0.1:${port} max_conns=1;"$'\n'
  # health upstream has NO cap, so readiness probes still reach a worker mid-stream.
  HEALTH_SERVERS+="        server 127.0.0.1:${port};"$'\n'
done

cat > /etc/nginx/nginx.conf <<EOF
worker_processes 1;
error_log /dev/stderr warn;
pid /run/nginx.pid;
events { worker_connections 1024; }
http {
    access_log off;
    upstream soulx_ws {
        least_conn;
${WS_SERVERS}    }
    upstream soulx_health {
        least_conn;
${HEALTH_SERVERS}    }
    map \$http_upgrade \$connection_upgrade { default upgrade; '' close; }
    server {
        listen ${PUBLIC_PORT};
        # Long-lived bidirectional WebSocket: keep upgrade headers, disable
        # buffering, and use long timeouts so an idle avatar isn't dropped.
        location /ws {
            proxy_pass http://soulx_ws;
            proxy_http_version 1.1;
            proxy_set_header Upgrade \$http_upgrade;
            proxy_set_header Connection \$connection_upgrade;
            proxy_set_header Host \$host;
            proxy_read_timeout  3600s;
            proxy_send_timeout  3600s;
            proxy_buffering off;
        }
        location /health {
            proxy_pass http://soulx_health;
            proxy_http_version 1.1;
        }
    }
}
EOF

# ── Render supervisord config (nginx + K workers, crash-restart) ─────────────
SUP_CONF=/etc/supervisor/supervisord.conf
mkdir -p /etc/supervisor /var/log/supervisor
cat > "$SUP_CONF" <<EOF
[supervisord]
nodaemon=true
logfile=/dev/null
logfile_maxbytes=0

[program:nginx]
command=nginx -g 'daemon off;'
autorestart=true
priority=50
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0
EOF

for i in $(seq 0 $((POOL_SIZE - 1))); do
  port=$((BASE_PORT + i))
  cat >> "$SUP_CONF" <<EOF

[program:soulx-worker-${i}]
command=python -m uvicorn soulx_server:app --host 127.0.0.1 --port ${port}
directory=${APP_DIR}
environment=SOULX_WORKER_ID="${i}"
autorestart=true
startsecs=20
stopwaitsecs=30
priority=10
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0
EOF
done

exec supervisord -c "$SUP_CONF"
