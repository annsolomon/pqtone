#!/bin/bash
# Runs once, at first initialisation, as the bootstrap superuser.
# Creates the three least-privilege roles and the pqt schema owned by the migrator.
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v dbname="$POSTGRES_DB" \
     -v migrator_pw="$PQT_DB_MIGRATOR_PASSWORD" \
     -v app_pw="$PQT_DB_APP_PASSWORD" \
     -v read_pw="$PQT_DB_READ_PASSWORD" <<'SQL'
CREATE ROLE pqt_migrator LOGIN PASSWORD :'migrator_pw' CONNECTION LIMIT 5;
CREATE ROLE pqt_app      LOGIN PASSWORD :'app_pw'      CONNECTION LIMIT 60;
CREATE ROLE pqt_read     LOGIN PASSWORD :'read_pw'     CONNECTION LIMIT 10;

REVOKE ALL ON DATABASE :"dbname" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"dbname" TO pqt_migrator, pqt_app, pqt_read;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM PUBLIC;

CREATE SCHEMA pqt AUTHORIZATION pqt_migrator;
ALTER ROLE pqt_migrator SET search_path = pqt;
ALTER ROLE pqt_app      SET search_path = pqt;
ALTER ROLE pqt_read     SET search_path = pqt;
ALTER ROLE pqt_app      SET statement_timeout = '15s';
ALTER ROLE pqt_read     SET statement_timeout = '60s';
ALTER ROLE pqt_read     SET default_transaction_read_only = on;
ALTER DATABASE :"dbname" SET timezone TO 'UTC';
SQL

# TCP connections must use TLS (hostssl); local socket access stays for health checks.
sed -i -E 's/^host(\s+all\s+all\s+all\s+)/hostssl\1/' "$PGDATA/pg_hba.conf"
echo "pqt roles created; TCP connections now require TLS"
