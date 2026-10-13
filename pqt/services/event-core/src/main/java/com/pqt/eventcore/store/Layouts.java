package com.pqt.eventcore.store;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.pqt.eventcore.config.PqtProperties;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.regex.Pattern;

/**
 * Store layouts (floor plans) from the layouts folder. A path is never built from a request: the store id is
 * checked against its pattern and then matched against the names of the files that exist in the folder.
 */
@Component
public class Layouts {
    public static final Pattern STORE_ID = Pattern.compile("^[a-z0-9-]{1,64}$");

    private final ObjectMapper mapper;
    private final PqtProperties props;

    public Layouts(ObjectMapper mapper, PqtProperties props) {
        this.mapper = mapper;
        this.props = props;
    }

    /** The layout file for this store, or null when there is none. The id must already match STORE_ID. */
    public Path path(String storeId) throws IOException {
        if (storeId == null || !STORE_ID.matcher(storeId).matches()) throw new IllegalArgumentException("invalid store id");
        String wanted = storeId + ".json";
        try (DirectoryStream<Path> ds = Files.newDirectoryStream(Path.of(props.paths().layouts()), "*.json")) {
            for (Path p : ds) {
                if (Files.isRegularFile(p) && p.getFileName().toString().equals(wanted)) return p;
            }
        }
        return null;
    }

    public Optional<JsonNode> read(String storeId) throws IOException {
        Path p = path(storeId);
        return p == null ? Optional.empty() : Optional.of(mapper.readTree(Files.readAllBytes(p)));
    }

    /** Ids of the zones of one kind (entrance, sales, monitored, checkout), in layout order. */
    public static List<String> zonesOfKind(JsonNode layout, String kind) {
        List<String> out = new ArrayList<>();
        for (JsonNode z : layout.path("zones")) {
            if (kind.equals(z.path("kind").asText())) out.add(z.path("id").asText());
        }
        return out;
    }

    /** Ids of the queues, in layout order. */
    public static List<String> queues(JsonNode layout) {
        List<String> out = new ArrayList<>();
        for (JsonNode q : layout.path("queues")) out.add(q.path("id").asText());
        return out;
    }

    /**
     * The registers (tills) that serve a queue: those naming it, and those naming no queue when it is the first
     * queue (milestone S2: a register without queueId serves the first queue).
     */
    public static List<String> registersServing(JsonNode layout, String queueId) {
        List<String> queues = queues(layout);
        boolean first = !queues.isEmpty() && queues.get(0).equals(queueId);
        List<String> out = new ArrayList<>();
        for (JsonNode r : layout.path("registers")) {
            String serves = r.hasNonNull("queueId") ? r.get("queueId").asText() : null;
            if (queueId.equals(serves) || (serves == null && first)) out.add(r.path("id").asText());
        }
        return out;
    }
}
