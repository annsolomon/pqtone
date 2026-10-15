package com.pqt.eventcore;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.pqt.eventcore.store.Layouts;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * Entrance zones, queues, and which registers (tills) serve which queue, on layouts shaped like the repository's
 * store-001 (one queue, registers naming none) and store-002 (two queues, each register naming its own). Inline,
 * because the image build that runs these tests has no config/ folder.
 */
class LayoutsTest {
    static final ObjectMapper M = new ObjectMapper();

    static final String ONE_QUEUE = """
            {"storeId":"store-001","zones":[{"id":"entrance","kind":"entrance"},{"id":"produce","kind":"sales"},
              {"id":"checkout","kind":"checkout"}],
             "queues":[{"id":"checkout-1","zoneId":"checkout"}],
             "registers":[{"id":"reg-1"},{"id":"reg-2"},{"id":"reg-3"},{"id":"reg-4"}]}""";

    static final String TWO_QUEUES = """
            {"storeId":"store-002","zones":[{"id":"entrance","kind":"entrance"},{"id":"side-door","kind":"entrance"},
              {"id":"checkout-a","kind":"checkout"},{"id":"checkout-b","kind":"checkout"}],
             "queues":[{"id":"checkout-1","zoneId":"checkout-a"},{"id":"checkout-2","zoneId":"checkout-b"}],
             "registers":[{"id":"reg-1","queueId":"checkout-1"},{"id":"reg-2","queueId":"checkout-1"},
                          {"id":"reg-3","queueId":"checkout-2"},{"id":"reg-4"}]}""";

    @Test
    void registersNamingNoQueueServeTheFirstQueue() throws Exception {
        JsonNode l = M.readTree(ONE_QUEUE);
        assertEquals(List.of("entrance"), Layouts.zonesOfKind(l, "entrance"));
        assertEquals(List.of("checkout-1"), Layouts.queues(l));
        assertEquals(List.of("reg-1", "reg-2", "reg-3", "reg-4"), Layouts.registersServing(l, "checkout-1"));
        assertEquals(List.of(), Layouts.registersServing(l, "checkout-9"));
    }

    @Test
    void registersNamingAQueueServeOnlyThatQueue() throws Exception {
        JsonNode l = M.readTree(TWO_QUEUES);
        assertEquals(List.of("entrance", "side-door"), Layouts.zonesOfKind(l, "entrance"), "every entrance, in layout order");
        assertEquals(List.of("reg-1", "reg-2", "reg-4"), Layouts.registersServing(l, "checkout-1"),
                "reg-4 names no queue, so it serves the first");
        assertEquals(List.of("reg-3"), Layouts.registersServing(l, "checkout-2"));
    }

    @Test
    void aLayoutWithoutRegistersOrEntrancesHasNone() throws Exception {
        JsonNode l = M.readTree("{\"storeId\":\"s\",\"zones\":[],\"queues\":[]}");
        assertEquals(List.of(), Layouts.zonesOfKind(l, "entrance"));
        assertEquals(List.of(), Layouts.registersServing(l, "checkout-1"));
    }
}
