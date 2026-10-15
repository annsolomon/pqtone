package com.pqt.eventcore;

import com.pqt.eventcore.config.PqtProperties;
import com.pqt.eventcore.ingest.SourcePolicy;
import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Which producer may publish which source, and (X1 B3, ADR-036) which store an edge source may write. */
class SourcePolicyTest {
    final SourcePolicy policy = new SourcePolicy(new PqtProperties.Ingest(Duration.ofMinutes(5), Duration.ofHours(24), 500,
            65536, 1048576, 100, 200, 1, "urn:pqt:sim:", Map.of("edge-demo", List.of("urn:pqt:edge:store-001")),
            List.of("urn:pqt:sim:", "urn:pqt:edge:")));

    @Test
    void aClientPublishesOnlyItsOwnSources() {
        assertTrue(policy.clientMayPublish("edge-demo", "urn:pqt:edge:store-001:pos-file"));
        assertFalse(policy.clientMayPublish("edge-demo", "urn:pqt:edge:store-999:gw-1"));
        assertFalse(policy.clientMayPublish("someone-else", "urn:pqt:edge:store-001:pos-file"));
        assertFalse(policy.clientMayPublish(null, "urn:pqt:edge:store-001:pos-file"));
    }

    @Test
    void anEdgeSourceWritesOnlyTheStoreItNames() {
        assertTrue(SourcePolicy.storeMatchesSource("urn:pqt:edge:store-001:pos-file", "store-001"));
        assertTrue(SourcePolicy.storeMatchesSource("urn:pqt:edge:store-001", "store-001"));
        assertFalse(SourcePolicy.storeMatchesSource("urn:pqt:edge:store-001:pos-file", "store-002"));
        assertFalse(SourcePolicy.storeMatchesSource("urn:pqt:edge:store-001:cam-1", "store-0012"));
        assertFalse(SourcePolicy.storeMatchesSource("urn:pqt:edge:store-0012:cam-1", "store-001"), "a prefix is not a match");
        assertFalse(SourcePolicy.storeMatchesSource("urn:pqt:edge:store-001:cam-1", null));
    }

    @Test
    void simulatorSourcesKeepTheirOwnNaming() {
        assertTrue(SourcePolicy.storeMatchesSource("urn:pqt:sim:store-sim:store-001", "store-002"));
    }
}
