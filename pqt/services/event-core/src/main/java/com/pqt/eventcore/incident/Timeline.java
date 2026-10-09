package com.pqt.eventcore.incident;

import java.time.Duration;
import java.time.Instant;
import java.util.Optional;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Milestone C4: which events explain an incident, and over what span of event time.
 *
 * Pure functions only (no I/O), so the window and subject rules are unit-tested without a database.
 * Everything here is event time: the incident's onset, detection and resolution as the rules engine
 * computed them from the events' {@code time} attribute.
 */
public final class Timeline {
    /** Context shown before the breach started. */
    public static final Duration BEFORE = Duration.ofMinutes(10);
    /** Context shown after the incident cleared. */
    public static final Duration AFTER = Duration.ofMinutes(5);
    /** Upper bound on the span, so a long-running or never-resolved incident can't scan a whole day. */
    public static final Duration MAX_SPAN = Duration.ofHours(2);
    /** Upper bound on series points returned; beyond this the response says it was truncated. */
    public static final int MAX_POINTS = 5000;
    /** Upper bound on markers (register opened / closed) returned. */
    public static final int MAX_MARKERS = 500;

    private static final Pattern SUBJECT = Pattern.compile("^(queue|zone):([a-z0-9-]{1,64})$");

    private Timeline() {
    }

    /** The event-time span to show: [onset - BEFORE, resolved + AFTER], capped at MAX_SPAN from its start. */
    public record Window(Instant from, Instant to) {
    }

    /** What the incident is about: a queue (series = queue length) or a zone (series = entries per minute). */
    public record Target(Kind kind, String id) {
    }

    public enum Kind {
        QUEUE, ZONE;

        public String json() {
            return name().toLowerCase(java.util.Locale.ROOT);
        }
    }

    public static Window window(Instant onset, Instant detected, Instant resolved) {
        if (onset == null || detected == null) throw new IllegalArgumentException("onset and detected are required");
        Instant from = onset.minus(BEFORE);
        Instant cap = from.plus(MAX_SPAN);
        Instant end = resolved != null ? resolved.plus(AFTER) : cap;
        Instant floor = detected.plus(AFTER);
        if (end.isBefore(floor)) end = floor;
        if (end.isAfter(cap)) end = cap;
        return new Window(from, end);
    }

    /** Parses the incident subject the rules engine writes ({@code queue:<id>} or {@code zone:<id>}). */
    public static Optional<Target> target(String subject) {
        if (subject == null) return Optional.empty();
        Matcher m = SUBJECT.matcher(subject);
        if (!m.matches()) return Optional.empty();
        return Optional.of(new Target(m.group(1).equals("queue") ? Kind.QUEUE : Kind.ZONE, m.group(2)));
    }
}
