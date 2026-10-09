package com.pqt.rules.kafka;

import com.pqt.rules.app.RuleSet;
import com.pqt.rules.app.VersionCheck;
import com.pqt.rules.domain.FootfallSpikeDetector;
import com.pqt.rules.domain.RuleEngine;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.List;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicReference;

/**
 * The rules the engine is running right now (milestone R4). Starts from the file baked into the
 * deployment; a document published to the compacted rules.config.v1 topic replaces it without a
 * restart, if VersionCheck allows; a tombstone on that topic goes back to the file.
 *
 * <p>Stream threads read {@link #current()} for every record; the global-store thread swaps it.
 * Each {@link Active} is immutable, so a record is always evaluated against one consistent set.
 */
public final class RulesHolder {
    private static final Logger log = LoggerFactory.getLogger(RulesHolder.class);

    public record Active(RuleSet rules, RuleEngine engine, Optional<FootfallSpikeDetector> footfall, String source) {
    }

    private final RuleSet file;
    private final AtomicReference<Active> active;

    public RulesHolder(RuleSet file) {
        this.file = file;
        this.active = new AtomicReference<>(activate(file, "file"));
    }

    /** A holder that never changes, for tests and the offline runner. */
    public static RulesHolder fixed(RuleEngine engine) {
        return new RulesHolder(RuleSet.of(engine.config(), Optional.empty()), engine);
    }

    private RulesHolder(RuleSet set, RuleEngine engine) {
        this.file = set;
        this.active = new AtomicReference<>(new Active(set, engine, Optional.empty(), "file"));
    }

    private static Active activate(RuleSet set, String source) {
        return new Active(set, new RuleEngine(set.core()), set.footfall().map(FootfallSpikeDetector::new), source);
    }

    public Active current() {
        return active.get();
    }

    /**
     * Applies a published document. Returns the reasons it was refused; empty when it is now active
     * (or already was). The running rules are never left half-changed.
     */
    public synchronized List<String> publish(String text) {
        RuleSet next;
        try {
            next = RuleSet.parse(text);
        } catch (RuntimeException e) {   // any invalid document, whatever the parser throws
            log.error("refused published rules: {}", e.toString());
            return List.of(String.valueOf(e.getMessage()));
        }
        Active now = active.get();
        if (next.sha256().equals(now.rules().sha256())) return List.of();
        List<String> problems = VersionCheck.structural(file, next);
        if (problems.isEmpty()) problems = VersionCheck.problems(now.rules(), next);
        if (!problems.isEmpty()) {
            log.error("refused published rules {}: {}", next.sha256(), problems);
            return problems;
        }
        active.set(activate(next, "topic"));
        log.info("rules {} active (from topic), versions {}", next.sha256(), next.versions());
        return List.of();
    }

    /** A tombstone on the topic: back to the deployment's file. */
    public synchronized void reset() {
        active.set(activate(file, "file"));
        log.info("rules reset to the file {}, versions {}", file.sha256(), file.versions());
    }
}
