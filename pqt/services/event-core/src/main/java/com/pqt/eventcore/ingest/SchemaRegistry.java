package com.pqt.eventcore.ingest;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.networknt.schema.JsonSchema;
import com.networknt.schema.JsonSchemaFactory;
import com.networknt.schema.SpecVersion;
import com.networknt.schema.ValidationMessage;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.regex.Pattern;

/** Loads schemas/catalog.json: the envelope schema plus one data schema per (type, dataschema). */
public final class SchemaRegistry {

    public record TypeEntry(String type, Map<String, JsonSchema> dataSchemas, Pattern subjectPattern) {
    }

    private final JsonSchema envelope;
    private final Map<String, TypeEntry> types = new HashMap<>();

    public SchemaRegistry(Path dir, ObjectMapper mapper) throws IOException {
        JsonSchemaFactory factory = JsonSchemaFactory.getInstance(SpecVersion.VersionFlag.V202012);
        this.envelope = factory.getSchema(mapper.readTree(Files.readAllBytes(dir.resolve("envelope.schema.json"))));
        JsonNode catalog = mapper.readTree(Files.readAllBytes(dir.resolve("catalog.json")));
        for (JsonNode t : catalog.path("types")) {
            Map<String, JsonSchema> versions = new HashMap<>();
            for (JsonNode v : t.path("versions")) {
                JsonNode schema = mapper.readTree(Files.readAllBytes(dir.resolve(v.path("file").asText())));
                versions.put(v.path("dataschema").asText(), factory.getSchema(schema));
            }
            String type = t.path("type").asText();
            types.put(type, new TypeEntry(type, Map.copyOf(versions), Pattern.compile(t.path("subjectPattern").asText(".*"))));
        }
        if (types.isEmpty()) throw new IllegalStateException("schema catalog is empty: " + dir);
    }

    public List<String> validateEnvelope(JsonNode event) {
        return messages(envelope.validate(event), "envelope");
    }

    public Optional<TypeEntry> type(String type) {
        return Optional.ofNullable(types.get(type));
    }

    public Set<String> knownTypes() {
        return types.keySet();
    }

    static List<String> messages(Set<ValidationMessage> set, String prefix) {
        List<String> out = new ArrayList<>();
        for (ValidationMessage m : set) out.add(prefix + ": " + m.getMessage());
        out.sort(null);
        return out;
    }
}
