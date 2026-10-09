package com.pqt.rules.app;

import com.fasterxml.jackson.databind.node.ObjectNode;
import com.pqt.rules.domain.Incident;

import java.time.Instant;

/** Wire format of incidents on incidents.v1 / incidents.shadow.v1 (schemaVersion 1). */
public final class IncidentJson {
    private IncidentJson() {
    }

    public static String toJson(Incident inc) {
        ObjectNode n = Json.MAPPER.valueToTree(inc);
        n.put("schemaVersion", 1);
        n.put("onset", Instant.ofEpochMilli(inc.onsetMs).toString());
        n.put("detectedAt", Instant.ofEpochMilli(inc.detectedMs).toString());
        try {
            return Json.MAPPER.writeValueAsString(n);
        } catch (Exception ex) {
            throw new IllegalStateException(ex);
        }
    }
}
