#!/bin/sh
set -eu
umask 077

: "${REDIS_PASSWORD:?A Redis password is required}"
: "${TLS_CERT_PEM:?A TLS certificate is required}"
: "${TLS_KEY_PEM:?A TLS private key is required}"
memory=${REDIS_MAXMEMORY_MIB:-1536}
case "$memory" in
    0*|*[!0-9]*) echo "REDIS_MAXMEMORY_MIB must be a positive integer" >&2; exit 1 ;;
esac

cd /opt/redis-runtime
printf '%s\n' "$TLS_CERT_PEM" > tls.pem
printf '%s\n' "$TLS_KEY_PEM" > tls.key
# Quote each password byte in Redis config syntax, including quotes and newlines.
password=$(printf '%s' "$REDIS_PASSWORD" | od -An -v -tx1 | tr -d ' \n' | sed 's/../\\x&/g')
unset REDIS_PASSWORD TLS_CERT_PEM TLS_KEY_PEM

exec redis-server - <<EOF
bind 0.0.0.0
port 0
tls-port 6379
tls-auth-clients no
tls-cert-file /opt/redis-runtime/tls.pem
tls-key-file /opt/redis-runtime/tls.key
dir /opt/redis-runtime
save ""
appendonly no
shutdown-on-sigterm nosave
maxmemory ${memory}mb
maxmemory-policy allkeys-lru
user default reset on ">${password}" ~* &* +@all -@admin -@dangerous +info +client|setname +client|setinfo +client|id +client|list +cluster|nodes +cluster|slots +keys +scan +eval +evalsha +script|load
EOF
