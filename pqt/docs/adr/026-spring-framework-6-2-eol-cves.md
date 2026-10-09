# ADR-026: Accept two unexploitable Spring Framework 6.2 CVEs until the Spring Boot 4 migration

- **Status:** accepted, expires 2026-12-31
- **Date:** 2026-10-09
- **Milestone:** 0.6 (first CI run on GitHub)

## Context

CI scans every dependency with Trivy and fails on HIGH or CRITICAL findings that have a fix.
event-core runs on Spring Boot 3.5.16 (Spring Framework 6.2.19), the last open-source 3.5
release. Open-source support for Spring Boot 3.5 / Spring Framework 6.2 ended in June 2026.
Two CRITICAL CVEs published after that are fixed only in Spring Framework 7.0.9:

| CVE | What it needs to be exploitable | Does event-core have it? |
|---|---|---|
| CVE-2026-47884 (CVSS 9.8) | `XsltView` used for view rendering behind a `/**` mapping with no explicit view name | No. event-core is a REST API and BFF: controllers return JSON; there is no view resolver, template engine or `XsltView`. |
| CVE-2026-47890 (CVSS 9.8) | Server-Sent Events that render **view fragments** | No. `LiveHub` sends `SseEmitter` events whose data is JSON; no `FragmentsRendering` or views. |

`CLAUDE.md` pins the stack to Java 21 / Spring Boot 3, and moving to Spring Boot 4 (Spring
Framework 7, Spring Security 7, Jackson 3 by default, renamed starters) is a migration in its
own right, not a dependency bump inside milestone 0.6.

## Decision

1. Stay on Spring Boot 3.5.16 for milestone 0.6, with the newest patched versions of the
   libraries it manages where those exist (Jackson 2.21.7, pgjdbc 42.7.14, lz4-java 1.11.4).
2. Accept CVE-2026-47884 and CVE-2026-47890 in `.trivyignore` with an expiry of 2026-12-31.
   After that date CI fails again until they are fixed or re-reviewed.
3. Migrate event-core to Spring Boot 4.x before the first pilot, as its own milestone, and
   remove both entries in that PR. Update `CLAUDE.md` (Spring Boot 3 -> 4) in the same PR.

## Consequences

- CI is green without hiding anything: the two IDs, the reason and the expiry are in the repo
  and every scan still reports any new finding.
- Any new 6.2 CVE without an open-source fix will also need an entry, which is the signal to
  do the migration rather than add more exceptions.
- If event-core ever adds server-side views or view-fragment SSE, these entries must be
  removed first (the security-reviewer agent checks `.trivyignore` against this ADR).

## Alternatives considered

- **Migrate to Spring Boot 4 now.** Correct long term, but it changes the web, security,
  Kafka and JSON layers at once while CI is being made to run for the first time.
- **Commercial Spring support (6.2.x patches).** Not justified before a paying customer.
- **Raise the Trivy threshold.** Would hide every future critical, not just these two.
