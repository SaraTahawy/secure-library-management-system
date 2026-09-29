#!/bin/sh
set -eu

mkdir -p /etc/nginx/certs
if [ ! -f /etc/nginx/certs/local.crt ]; then
  openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout /etc/nginx/certs/local.key \
    -out /etc/nginx/certs/local.crt \
    -subj "/CN=localhost"
fi
