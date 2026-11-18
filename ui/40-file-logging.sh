#!/bin/sh
# Runs from the nginx image's /docker-entrypoint.d/ before nginx starts.
# With LOG_TO_FILE=true, nginx logs to /logs/ui-access.log and
# /logs/ui-error.log in addition to the container's stdout/stderr. The file
# is included inside the server block of nginx.conf, so the access log uses
# the same $dbbutler_loggable filter as stdout.
set -e

mkdir -p /etc/nginx/logging.d
case "$(echo "${LOG_TO_FILE:-false}" | tr '[:upper:]' '[:lower:]')" in
  true|1|yes|on)
    mkdir -p /logs
    cat > /etc/nginx/logging.d/file-logging.conf <<'EOF'
access_log /logs/ui-access.log main if=$dbbutler_loggable;
error_log /dev/stderr notice;
error_log /logs/ui-error.log notice;
EOF
    echo "40-file-logging: file logging enabled (/logs/ui-*.log)"
    ;;
esac
