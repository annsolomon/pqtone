package com.pip.eventcore.incident;

import com.fasterxml.jackson.databind.JsonNode;

import java.time.Instant;
import java.util.UUID;

public record IncidentView(UUID incidentId, String ruleId, String ruleVersion, String mode, String storeId,
                           String simRunId, String key, String subject, String severity, String summary,
                           Instant onsetAt, Instant detectedAt, Instant resolvedAt, String status,
                           JsonNode evidence, JsonNode attrs, Instant updatedAt, int version) {
}
