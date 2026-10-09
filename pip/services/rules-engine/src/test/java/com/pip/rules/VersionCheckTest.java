package com.pip.rules;

import com.pip.rules.app.RuleSet;
import com.pip.rules.app.VersionCheck;
import org.junit.jupiter.api.Test;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Milestone R4: which rules documents may replace the running one. */
class VersionCheckTest {
    static final String FILE = read();

    static String read() {
        try {
            return Files.readString(Path.of("../../config/rules.yaml"));
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    static String edit(String from, String to) {
        if (!FILE.contains(from)) throw new IllegalArgumentException("not in rules.yaml: " + from);
        return FILE.replace(from, to);
    }

    static final String Q_HEAD = "  - id: R-QUEUE-001\n    version: 1.0.0\n";

    @Test
    void theRepositoryFileParses() {
        RuleSet s = RuleSet.parse(FILE);
        assertEquals("1.0.0", s.versions().get("R-QUEUE-001"));
        assertTrue(s.footfall().isPresent());
        assertEquals(64, s.sha256().length());
    }

    @Test
    void semverOrdering() {
        assertTrue(VersionCheck.compare("1.0.10", "1.0.9") > 0);
        assertTrue(VersionCheck.compare("2.0.0", "1.9.9") > 0);
        assertEquals(0, VersionCheck.compare("1.2.3", "1.2.3"));
        assertNull(VersionCheck.parse("1.0"));
        assertNull(VersionCheck.parse("v1.0.0"));
        assertThrows(IllegalArgumentException.class, () -> VersionCheck.compare("x", "1.0.0"));
    }

    @Test
    void identicalOrVersionOnlyChangesAreAllowed() {
        RuleSet file = RuleSet.parse(FILE);
        assertEquals(List.of(), VersionCheck.problems(file, RuleSet.parse(FILE)));
        String bumpedOnly = edit(Q_HEAD, "  - id: R-QUEUE-001\n    version: 1.0.1\n");
        assertEquals(List.of(), VersionCheck.problems(file, RuleSet.parse(bumpedOnly)));
    }

    @Test
    void aChangedThresholdNeedsAHigherVersion() {
        RuleSet file = RuleSet.parse(FILE);
        String unbumped = edit("      threshold: 6 ", "      threshold: 4 ");
        assertEquals(List.of("R-QUEUE-001 changed but its version 1.0.0 is not higher than the running 1.0.0"),
                VersionCheck.problems(file, RuleSet.parse(unbumped)));
        String bumped = unbumped.replace(Q_HEAD, "  - id: R-QUEUE-001\n    version: 1.1.0\n");
        assertEquals(List.of(), VersionCheck.problems(file, RuleSet.parse(bumped)));
        String lower = unbumped.replace(Q_HEAD, "  - id: R-QUEUE-001\n    version: 0.9.0\n");
        assertEquals(1, VersionCheck.problems(file, RuleSet.parse(lower)).size());
    }

    @Test
    void modeAndSeverityChangesCountToo() {
        RuleSet file = RuleSet.parse(FILE);
        String shadow = edit("    mode: enforce          # enforce | shadow | off\n    severity: medium\n",
                "    mode: shadow\n    severity: medium\n");
        assertEquals(1, VersionCheck.problems(file, RuleSet.parse(shadow)).size());
    }

    @Test
    void structuralChangesNeedARestart() {
        RuleSet file = RuleSet.parse(FILE);
        String grace = FILE.replaceFirst("(?m)^grace: PT30S", "grace: PT45S");
        assertTrue(VersionCheck.problems(file, RuleSet.parse(grace)).get(0).startsWith("grace changed"));
        String window = edit("      window: PT5M ", "      window: PT10M ");
        assertTrue(VersionCheck.structural(file, RuleSet.parse(window)).get(0).startsWith("R-FOOT-001 window"));
    }

    @Test
    void badDocumentsAreRejected() {
        assertThrows(IllegalArgumentException.class, () -> RuleSet.parse("rules: ["));
        assertThrows(IllegalArgumentException.class, () -> RuleSet.parse("- just a list"));
        assertThrows(IllegalArgumentException.class, () -> RuleSet.parse(edit(Q_HEAD, "  - id: R-QUEUE-001\n    version: one\n")));
    }
}
