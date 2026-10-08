#!/bin/sh
# Generates a throw-away local CA and a TLS certificate for PostgreSQL (SAN: postgres).
# The CA private key is deleted immediately, so no further certificates can be minted from it.
# Production uses certificates from your PKI / cert-manager instead.
set -eu
D=/certs
if [ -s "$D/ca.crt" ] && [ -s "$D/postgres.crt" ] && [ -s "$D/postgres.key" ]; then
  echo "certificates already present"; exit 0
fi
apk add --no-cache openssl >/dev/null
umask 077
openssl req -x509 -newkey rsa:3072 -nodes -days 825 -subj "/CN=pip-local-ca" \
  -keyout "$D/ca.key" -out "$D/ca.crt" 2>/dev/null
openssl req -newkey rsa:3072 -nodes -subj "/CN=postgres" \
  -keyout "$D/postgres.key" -out "$D/postgres.csr" 2>/dev/null
printf 'subjectAltName=DNS:postgres\nextendedKeyUsage=serverAuth\nkeyUsage=digitalSignature,keyEncipherment\n' > "$D/ext.cnf"
openssl x509 -req -in "$D/postgres.csr" -CA "$D/ca.crt" -CAkey "$D/ca.key" -CAcreateserial \
  -days 825 -sha256 -extfile "$D/ext.cnf" -out "$D/postgres.crt" 2>/dev/null
rm -f "$D/ca.key" "$D/ca.srl" "$D/postgres.csr" "$D/ext.cnf"
chown 70:70 "$D/postgres.key"   # postgres user in the alpine image
chmod 600 "$D/postgres.key"
chmod 644 "$D/ca.crt" "$D/postgres.crt"
chmod 755 "$D"
echo "certificates generated"
