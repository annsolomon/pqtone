package com.pip.eventcore.it;

import com.zaxxer.hikari.HikariConfig;
import com.zaxxer.hikari.HikariDataSource;
import org.flywaydb.core.Flyway;
import org.flywaydb.core.api.MigrationVersion;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.Statement;
import java.util.UUID;

/**
 * A real PostgreSQL 16 set up the way production is: the three least-privilege roles from
 * deploy/postgres/init/00-roles.sh, the pip schema owned by pip_migrator, and the Flyway
 * migrations applied by pip_migrator. Tests then connect as pip_app, like event-core does.
 *
 * Passwords are random per run; nothing here is a real credential.
 */
public final class Db implements AutoCloseable {
    /** Same major and minor as deploy/compose/docker-compose.yml. */
    public static final DockerImageName IMAGE = DockerImageName.parse("postgres:16.4-alpine");

    public final PostgreSQLContainer<?> container;
    final String migratorPassword = UUID.randomUUID().toString();
    final String appPassword = UUID.randomUUID().toString();
    final String readPassword = UUID.randomUUID().toString();
    private HikariDataSource app;

    @SuppressWarnings("resource")
    public Db() {
        container = new PostgreSQLContainer<>(IMAGE).withDatabaseName("pip");
    }

    /** Starts the container and creates roles and schema. Does not migrate. */
    public Db start() throws Exception {
        container.start();
        try (Connection c = DriverManager.getConnection(container.getJdbcUrl(), container.getUsername(), container.getPassword());
             Statement s = c.createStatement()) {
            createRole(c, "pip_migrator", migratorPassword, 5);
            createRole(c, "pip_app", appPassword, 60);
            createRole(c, "pip_read", readPassword, 10);
            s.execute("REVOKE ALL ON DATABASE pip FROM PUBLIC");
            s.execute("GRANT CONNECT ON DATABASE pip TO pip_migrator, pip_app, pip_read");
            s.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC");
            s.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC");
            s.execute("CREATE SCHEMA pip AUTHORIZATION pip_migrator");
            s.execute("ALTER ROLE pip_migrator SET search_path = pip");
            s.execute("ALTER ROLE pip_app SET search_path = pip");
            s.execute("ALTER ROLE pip_read SET search_path = pip");
            s.execute("ALTER ROLE pip_app SET statement_timeout = '15s'");
            s.execute("ALTER ROLE pip_read SET default_transaction_read_only = on");
            s.execute("ALTER DATABASE pip SET timezone TO 'UTC'");
        }
        return this;
    }

    /**
     * CREATE ROLE cannot take a bind parameter, so the password travels as a session setting and is
     * quoted by format(%L) inside the server. No SQL text is ever built from a value.
     */
    private static void createRole(Connection c, String role, String password, int connectionLimit) throws Exception {
        try (var set = c.prepareStatement("SELECT set_config('pip_it.password', ?, false), set_config('pip_it.role', ?, false), "
                + "set_config('pip_it.limit', ?, false)")) {
            set.setString(1, password);
            set.setString(2, role);
            set.setString(3, Integer.toString(connectionLimit));
            set.execute();
        }
        try (Statement s = c.createStatement()) {
            s.execute("""
                    DO $$ BEGIN
                      EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L CONNECTION LIMIT %s',
                                     current_setting('pip_it.role'), current_setting('pip_it.password'),
                                     current_setting('pip_it.limit')::int);
                    END $$""");
            s.execute("SELECT set_config('pip_it.password', '', false)");
        }
    }

    /** Flyway as pip_migrator, exactly like the compose `flyway` service; target null = latest. */
    public void migrate(String target) {
        var cfg = Flyway.configure()
                .dataSource(container.getJdbcUrl(), "pip_migrator", migratorPassword)
                .schemas("pip")
                .createSchemas(false)
                .locations("classpath:db/migration")
                .validateMigrationNaming(true);
        if (target != null) cfg.target(MigrationVersion.fromVersion(target));
        cfg.load().migrate();
    }

    public void migrate() {
        migrate(null);
    }

    /** A pooled DataSource for pip_app, the role event-core runs as. */
    public synchronized HikariDataSource app() {
        if (app == null) {
            HikariConfig h = new HikariConfig();
            h.setJdbcUrl(container.getJdbcUrl());
            h.setUsername("pip_app");
            h.setPassword(appPassword);
            h.setMaximumPoolSize(10);
            app = new HikariDataSource(h);
        }
        return app;
    }

    /** A plain connection as the bootstrap superuser, for assertions only. */
    public Connection superuser() throws Exception {
        return DriverManager.getConnection(container.getJdbcUrl(), container.getUsername(), container.getPassword());
    }

    @Override
    public void close() {
        if (app != null) app.close();
        container.stop();
    }
}
