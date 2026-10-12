# syntax=docker/dockerfile:1.7
FROM maven:3.9.9-eclipse-temurin-21 AS build
WORKDIR /src
COPY schemas /src/schemas
COPY services/event-core/pom.xml /src/services/event-core/pom.xml
WORKDIR /src/services/event-core
COPY services/event-core/src ./src
# Integration tests need Docker, which an image build does not have; make test-it runs them.
RUN --mount=type=cache,target=/root/.m2 mvn -B -ntp -DskipITs verify

# glibc base: the zstd native library used by the Kafka client needs it.
FROM eclipse-temurin:21-jre
# The base image ships Canonical's pebble service manager (/usr/bin/pebble), which nothing here runs; it is
# removed so its Go runtime CVEs (e.g. CVE-2026-78667, -78669, -97031 on 12 Oct 2026) are not in our image.
RUN apt-get update && apt-get install -y --no-install-recommends wget && rm -rf /var/lib/apt/lists/* \
 && rm -f /usr/bin/pebble \
 && groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --no-create-home app
WORKDIR /app
COPY --from=build /src/services/event-core/target/event-core.jar /app/event-core.jar
COPY schemas /app/schemas
COPY config/layouts /app/layouts
USER 10001:10001
EXPOSE 8080 8081
HEALTHCHECK --interval=10s --timeout=3s --start-period=90s --retries=12 \
  CMD wget -qO- http://127.0.0.1:8081/actuator/health/readiness || exit 1
ENTRYPOINT ["java", "-XX:MaxRAMPercentage=75", "-XX:+ExitOnOutOfMemoryError", "-jar", "/app/event-core.jar"]
