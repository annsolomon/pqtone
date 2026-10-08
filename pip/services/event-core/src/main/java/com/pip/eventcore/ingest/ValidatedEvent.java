package com.pip.eventcore.ingest;

import com.fasterxml.jackson.databind.JsonNode;

import java.time.Instant;

/** An event that passed every contract check, plus its canonical payload hash. */
public record ValidatedEvent(String id, String source, String type, Instant time, String subject,
                             String dataschema, String storeId, String simRunId, String traceparent,
                             JsonNode node, String payloadHash) {
}
