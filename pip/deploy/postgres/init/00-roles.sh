#!/bin/bash
# Runs once, at first initialisation, as the bootstrap superuser.
# Creates the three least-privilege roles and the pip schema owned by the migrator.
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v dbname="$POSTGRES_DB" \
     -v migrator_pw="$PIP_DB_MIGRATOR_PASSWORD" \
     -v app_pw="$PIP_DB_APP_PASSWORD" \
     -v read_pw="$PIP_DB_READ_PASSWORD" <<'SQL'
CREATE ROLE pip_migrator LOGIN PASSWORD :'migrator_pw' CONNECTION LIMIT 5;
CREATE ROLE pip_app      LOGIN PASSWORD :'app_pw'      CONNECTION LIMIT 60;
CREATE ROLE pip_read     LOGIN PASSWORD :'read_pw'     CONNECTION LIMIT 10;

REVOKE ALL ON DATABASE :"dbname" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"dbname" TO pip_migrator, pip_app, pip_read;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM PUBLIC;

CREATE SCHEMA pip AUTHORIZATION pip_migrator;
ALTER ROLE pip_migrator SET search_path = pip;
ALTER ROLE pip_app      SET search_path = pip;
ALTER ROLE pip_read     SET search_path = pip;
ALTER ROLE pip_app      SET statement_timeout = '15s';
ALTER ROLE pip_read     SET statement_timeout = '60s';
ALTER ROLE pip_read     SET default_transaction_read_only = on;
ALTER DATABASE :"dbname" SET timezone TO 'UTC';
SQL

# TCP connections must use TLS (hostssl); local socket access stays for health checks.
sed -i -E 's/^host(\s+all\s+all\s+all\s+)/hostssl\1/' "$PGDATA/pg_hba.conf"
echo "pip roles created; TCP connections now require TLS"
