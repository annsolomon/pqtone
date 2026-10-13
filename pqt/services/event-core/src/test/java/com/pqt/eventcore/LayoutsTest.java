package com.pqt.eventcore;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.pqt.eventcore.store.Layouts;
import org.junit.jupiter.api.Test;

import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;

/** The repository's own layouts: entrance zones, queues, and which registers (tills) serve which queue. */
class LayoutsTest {
    static final ObjectMapper M = new ObjectMapper();

    static JsonNode layout(String store) throws Exception {
        return M.readTree(Path.of(System.getProperty("pqt.schemas", "../../schemas")).resolve("../config/layouts/" + store + ".json").toFile());
    }

    @Test
    void storeOneHasOneEntranceOneQueueAndFourRegistersServingIt() throws Exception {
        JsonNode l = layout("store-001");
        assertEquals(List.of("entrance"), Layouts.zonesOfKind(l, "entrance"));
        assertEquals(List.of("checkout-1"), Layouts.queues(l));
        assertEquals(List.of("reg-1", "reg-2", "reg-3", "reg-4"), Layouts.registersServing(l, "checkout-1"),
                "registers naming no queue serve the first queue");
        assertEquals(List.of(), Layouts.registersServing(l, "checkout-9"));
    }

    @Test
    void storeTwoSplitsItsRegistersBetweenTwoQueues() throws Exception {
        JsonNode l = layout("store-002");
        List<String> queues = Layouts.queues(l);
        assertEquals(2, queues.size());
        List<String> first = Layouts.registersServing(l, queues.get(0));
        List<String> second = Layouts.registersServing(l, queues.get(1));
        assertEquals(List.of(), first.stream().filter(second::contains).toList(), "no register serves both queues");
        assertEquals(l.path("registers").size(), first.size() + second.size(), "every register serves exactly one queue");
    }
}
