package com.pqt.eventcore;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.pqt.eventcore.config.PqtProperties;
import com.pqt.eventcore.store.StoreController;
import com.pqt.eventcore.web.ApiException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.http.HttpStatus;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

/** Layouts are served only from files that exist in the layouts folder, never from a path built from the request. */
class StoreLayoutTest {
    @TempDir
    Path root;

    private StoreController controller;

    @BeforeEach
    void setUp() throws Exception {
        Path layouts = Files.createDirectory(root.resolve("layouts"));
        Files.writeString(layouts.resolve("store-001.json"), "{\"storeId\":\"store-001\",\"name\":\"One\"}");
        Files.writeString(layouts.resolve("notes.txt"), "not a layout");
        Files.writeString(root.resolve("secret.json"), "{\"storeId\":\"secret\",\"name\":\"outside\"}");
        PqtProperties props = new PqtProperties(null, null, null,
                new PqtProperties.Paths(root.resolve("schemas").toString(), layouts.toString()), null, null);
        controller = new StoreController(null, new ObjectMapper(), props);
    }

    @Test
    void servesAKnownLayout() throws Exception {
        JsonNode n = controller.layout("store-001");
        assertEquals("One", n.path("name").asText());
    }

    @Test
    void unknownStoreIsNotFound() {
        ApiException e = assertThrows(ApiException.class, () -> controller.layout("store-999"));
        assertEquals(HttpStatus.NOT_FOUND, e.status());
    }

    @ParameterizedTest
    @ValueSource(strings = {"../secret", "..", "store-001.json", "STORE-001", "a/b", "", "store 001", "%2e%2e"})
    void malformedIdsAreRejected(String id) {
        ApiException e = assertThrows(ApiException.class, () -> controller.layout(id));
        assertEquals(HttpStatus.BAD_REQUEST, e.status());
    }

    @Test
    void wellFormedIdOutsideTheFolderIsNotFound() {
        // "secret" passes the id pattern but its file lives outside the layouts folder.
        ApiException e = assertThrows(ApiException.class, () -> controller.layout("secret"));
        assertEquals(HttpStatus.NOT_FOUND, e.status());
    }

    @Test
    void storesListsOnlyJsonLayouts() throws Exception {
        List<Map<String, Object>> stores = controller.stores();
        assertEquals(List.of(Map.of("storeId", "store-001", "name", "One")), stores);
    }
}
