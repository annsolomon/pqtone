# syntax=docker/dockerfile:1.7
FROM maven:3.9.9-eclipse-temurin-21 AS build
WORKDIR /src/services/rules-engine
COPY services/rules-engine/pom.xml ./pom.xml
COPY services/rules-engine/src ./src
# Tests read the repository's rules file (../../config/rules.yaml), as they do in make test-fast.
COPY config/rules.yaml /src/config/rules.yaml
RUN --mount=type=cache,target=/root/.m2 mvn -B -ntp verify

# glibc base: RocksDB and zstd native libraries do not load on Alpine/musl.
FROM eclipse-temurin:21-jre
# The base image ships Canonical's pebble service manager (/usr/bin/pebble), which nothing here runs; it is
# removed so its Go runtime CVEs (e.g. CVE-2026-78667, -78669, -97031 on 12 Oct 2026) are not in our image.
RUN apt-get update && apt-get install -y --no-install-recommends wget && rm -rf /var/lib/apt/lists/* \
 && rm -f /usr/bin/pebble \
 && groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --no-create-home app \
 && mkdir -p /var/lib/rules-engine/state && chown -R 10001:10001 /var/lib/rules-engine
WORKDIR /app
COPY --from=build /src/services/rules-engine/target/rules-engine.jar /app/rules-engine.jar
COPY config/rules.yaml /config/rules.yaml
USER 10001:10001
EXPOSE 8082
HEALTHCHECK --interval=10s --timeout=3s --start-period=60s --retries=12 \
  CMD wget -qO- http://127.0.0.1:8082/health/ready || exit 1
ENTRYPOINT ["java", "-XX:MaxRAMPercentage=75", "-XX:+ExitOnOutOfMemoryError", "-jar", "/app/rules-engine.jar"]
