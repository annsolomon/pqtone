package com.pip.eventcore;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.pip.eventcore.ingest.Canonical;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;

class CanonicalTest {
    final ObjectMapper m = new ObjectMapper();

    @Test
    void keyOrderAndWhitespaceDoNotChangeTheHash() throws Exception {
        String a = "{\"b\":1,\"a\":{\"y\":[1,2],\"x\":\"é\"}}";
        String b = "{ \"a\" : { \"x\":\"é\", \"y\":[1, 2] }, \"b\" : 1 }";
        assertEquals(Canonical.eventHash(m.readTree(a)), Canonical.eventHash(m.readTree(b)));
        assertEquals("{\"a\":{\"x\":\"é\",\"y\":[1,2]},\"b\":1}", Canonical.json(m.readTree(b)));
    }

    @Test
    void traceContextIsExcludedButPayloadIsNot() throws Exception {
        String base = "{\"id\":\"1\",\"data\":{\"n\":1}}";
        String traced = "{\"id\":\"1\",\"data\":{\"n\":1},\"traceparent\":\"00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01\"}";
        String changed = "{\"id\":\"1\",\"data\":{\"n\":2}}";
        assertEquals(Canonical.eventHash(m.readTree(base)), Canonical.eventHash(m.readTree(traced)));
        assertNotEquals(Canonical.eventHash(m.readTree(base)), Canonical.eventHash(m.readTree(changed)));
    }
}
