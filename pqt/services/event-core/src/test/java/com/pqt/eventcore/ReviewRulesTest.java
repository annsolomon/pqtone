package com.pqt.eventcore;

import com.pqt.eventcore.config.Roles;
import com.pqt.eventcore.incident.Transitions;
import com.pqt.eventcore.ingest.RateLimiter;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Optional;
import java.util.Set;
import java.util.concurrent.atomic.AtomicLong;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ReviewRulesTest {
    @Test
    void roleHierarchyExpands() {
        assertEquals(Set.of("viewer", "operator", "reviewer", "admin"), Roles.expand(List.of("admin")));
        assertEquals(Set.of("viewer", "operator"), Roles.expand(List.of("operator")));
        assertEquals(Set.of(), Roles.expand(List.of("superuser")));
        assertEquals(Set.of(), Roles.expand(null));
    }

    @Test
    void stateMachine() {
        assertEquals(Optional.of("ACKNOWLEDGED"), Transitions.next("OPEN", "ack"));
        assertEquals(Optional.of("CONFIRMED"), Transitions.next("ACKNOWLEDGED", "confirm"));
        assertEquals(Optional.of("DISMISSED"), Transitions.next("AUTO_RESOLVED", "dismiss"));
        assertEquals(Optional.of("CLOSED"), Transitions.next("CONFIRMED", "close"));
        assertTrue(Transitions.next("DISMISSED", "confirm").isEmpty());
        assertTrue(Transitions.next("CLOSED", "ack").isEmpty());
        assertEquals("operator", Transitions.requiredRole("ack"));
        assertEquals("reviewer", Transitions.requiredRole("dismiss"));
    }

    @Test
    void tokenBucketThrottlesAndRefills() {
        AtomicLong now = new AtomicLong(0);
        RateLimiter rl = new RateLimiter(10, 20, now::get);
        assertEquals(0, rl.acquire("c", 20));
        assertTrue(rl.acquire("c", 5) > 0);
        now.addAndGet(1_000_000_000L);
        assertEquals(0, rl.acquire("c", 5));
        assertEquals(0, rl.acquire("other", 20));
    }
}
