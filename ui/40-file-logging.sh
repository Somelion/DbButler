#!/bin/sh
# Runs from the nginx image's /docker-entrypoint.d/ before nginx starts.
# With LOG_TO_FILE=true, nginx logs to /logs/ui-access.log and
# /logs/ui-error.log in addition to the container's stdout/stderr.
set -e

case "$(echo "${LOG_TO_FILE:-false}" | tr '[:upper:]' '[:lower:]')" in
  true|1|yes|on)
    mkdir -p /logs
    cat > /etc/nginx/conf.d/00-file-logging.conf <<'EOF'
access_log /dev/stdout;
access_log /logs/ui-access.log;
error_log /dev/stderr notice;
error_log /logs/ui-error.log notice;
EOF
    echo "40-file-logging: file logging enabled (/logs/ui-*.log)"
    ;;
esac
