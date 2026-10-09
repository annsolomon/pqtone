package com.pqt.rules.domain;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/** Rule output. kind is OPENED or RESOLVED; incidentId is deterministic. */
public final class Incident {
    public String incidentId;
    public String ruleId;
    public String ruleVersion;
    public String mode;
    public String kind;
    public String storeId;
    public String simRunId;
    public String key;
    public String subject;
    public String severity;
    public String summary;
    public long onsetMs;
    public long detectedMs;
    public List<String> evidence = new ArrayList<>();
    public Map<String, Object> attrs = new TreeMap<>();

    public Incident() {
    }
}
