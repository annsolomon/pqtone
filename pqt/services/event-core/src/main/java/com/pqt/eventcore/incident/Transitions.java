package com.pqt.eventcore.incident;

import com.pqt.eventcore.config.Roles;

import java.util.Map;
import java.util.Optional;
import java.util.Set;

/** Review state machine. Pure, so it is unit-tested without a database. */
public final class Transitions {
    public static final Set<String> DISMISS_REASONS =
            Set.of("FALSE_POSITIVE", "DUPLICATE", "EXPECTED_BEHAVIOUR", "TEST_EVENT", "OTHER");

    private static final Map<String, Map<String, String>> NEXT = Map.of(
            "OPEN", Map.of("ack", "ACKNOWLEDGED", "confirm", "CONFIRMED", "dismiss", "DISMISSED"),
            "ACKNOWLEDGED", Map.of("confirm", "CONFIRMED", "dismiss", "DISMISSED"),
            "AUTO_RESOLVED", Map.of("confirm", "CONFIRMED", "dismiss", "DISMISSED"),
            "CONFIRMED", Map.of("close", "CLOSED"));

    private static final Map<String, String> REQUIRED_ROLE = Map.of(
            "ack", Roles.OPERATOR, "confirm", Roles.REVIEWER, "dismiss", Roles.REVIEWER, "close", Roles.REVIEWER);

    private Transitions() {
    }

    public static Optional<String> next(String status, String action) {
        return Optional.ofNullable(NEXT.getOrDefault(status, Map.of()).get(action));
    }

    public static String requiredRole(String action) {
        String r = REQUIRED_ROLE.get(action);
        if (r == null) throw new IllegalArgumentException("unknown action " + action);
        return r;
    }

    public static boolean isAction(String action) {
        return REQUIRED_ROLE.containsKey(action);
    }
}
