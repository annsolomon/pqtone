package com.pip.rules.app;

import com.fasterxml.jackson.databind.node.ObjectNode;
import org.apache.kafka.clients.CommonClientConfigs;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerConfig;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.common.TopicPartition;
import org.apache.kafka.common.config.SaslConfigs;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.apache.kafka.common.serialization.StringSerializer;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.List;
import java.util.Optional;
import java.util.Properties;

/**
 * Milestone R4: publish a rules document to the compacted rules.config.v1 topic, so every running
 * rules-engine switches to it without a restart. Refuses anything the engines would refuse:
 * invalid documents, structural changes (grace, R-FOOT-001 window/history/zones) and changed rules
 * whose version was not bumped.
 *
 * <pre>
 *   RulesPublisher --rules /work/new-rules.yaml [--baseline /config/rules.yaml] [--dry-run]
 *   RulesPublisher --reset            (tombstone: every engine goes back to its deployment file)
 * </pre>
 *
 * Kafka credentials come from PIP_KAFKA_BOOTSTRAP / PIP_KAFKA_USER / PIP_KAFKA_PASSWORD (an admin
 * principal: changing rules is an operator action, not something a service does on its own).
 */
public final class RulesPublisher {
    public static final String KEY = "rules";

    private RulesPublisher() {
    }

    public static void main(String[] args) throws Exception {
        String rules = null;
        String baseline = "/config/rules.yaml";
        boolean reset = false;
        boolean dryRun = false;
        for (int i = 0; i < args.length; i++) {
            switch (args[i]) {
                case "--rules" -> rules = args[++i];
                case "--baseline" -> baseline = args[++i];
                case "--reset" -> reset = true;
                case "--dry-run" -> dryRun = true;
                default -> fail("unknown argument " + args[i]);
            }
        }
        if (reset == (rules != null)) fail("use exactly one of --rules FILE or --reset");
        String topic = RulesEngineApp.env("PIP_TOPIC_RULES_CONFIG", "rules.config.v1");
        Properties p = kafka();

        if (reset) {
            if (!dryRun) send(p, topic, null);
            print("reset", null, List.of(), dryRun);
            return;
        }

        RuleSet proposed;
        try {
            proposed = RuleSet.parse(Files.readString(Path.of(rules)));
        } catch (RuntimeException e) {
            print("refused", null, List.of(String.valueOf(e.getMessage())), dryRun);
            System.exit(1);
            return;
        }
        RuleSet file = RuleSet.parse(Files.readString(Path.of(baseline)));
        RuleSet running = latest(p, topic).map(RuleSet::parse).orElse(file);
        List<String> problems = new java.util.ArrayList<>(VersionCheck.structural(file, proposed));
        problems.addAll(VersionCheck.problems(running, proposed));
        problems = problems.stream().distinct().toList();
        if (!problems.isEmpty()) {
            print("refused", proposed, problems, dryRun);
            System.exit(1);
            return;
        }
        if (proposed.sha256().equals(running.sha256())) {
            print("already-active", proposed, List.of(), dryRun);
            return;
        }
        if (!dryRun) send(p, topic, Files.readString(Path.of(rules)));
        print("published", proposed, List.of(), dryRun);
    }

    /** The document currently on the topic, if any (the last record of the single partition). */
    static Optional<String> latest(Properties base, String topic) {
        Properties c = new Properties();
        c.putAll(base);
        c.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class.getName());
        c.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class.getName());
        c.put(ConsumerConfig.ENABLE_AUTO_COMMIT_CONFIG, "false");
        c.put(ConsumerConfig.ISOLATION_LEVEL_CONFIG, "read_committed");
        try (KafkaConsumer<String, String> consumer = new KafkaConsumer<>(c)) {
            TopicPartition tp = new TopicPartition(topic, 0);
            consumer.assign(List.of(tp));
            consumer.seekToEnd(List.of(tp));
            long end = consumer.position(tp, Duration.ofSeconds(15));
            if (end == 0) return Optional.empty();
            consumer.seekToBeginning(List.of(tp));
            String value = null;
            boolean seen = false;
            long deadline = System.nanoTime() + Duration.ofSeconds(30).toNanos();
            while (consumer.position(tp) < end && System.nanoTime() < deadline) {
                for (ConsumerRecord<String, String> r : consumer.poll(Duration.ofMillis(500))) {
                    if (KEY.equals(r.key())) {
                        value = r.value();
                        seen = true;
                    }
                }
            }
            return seen ? Optional.ofNullable(value) : Optional.empty();
        }
    }

    private static void send(Properties base, String topic, String value) throws Exception {
        Properties pp = new Properties();
        pp.putAll(base);
        pp.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class.getName());
        pp.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, StringSerializer.class.getName());
        pp.put(ProducerConfig.ACKS_CONFIG, "all");
        pp.put(ProducerConfig.ENABLE_IDEMPOTENCE_CONFIG, "true");
        try (KafkaProducer<String, String> producer = new KafkaProducer<>(pp)) {
            producer.send(new ProducerRecord<>(topic, KEY, value)).get();
        }
    }

    private static Properties kafka() {
        Properties p = new Properties();
        p.put(CommonClientConfigs.BOOTSTRAP_SERVERS_CONFIG, RulesEngineApp.env("PIP_KAFKA_BOOTSTRAP", null));
        String protocol = RulesEngineApp.env("PIP_KAFKA_SECURITY_PROTOCOL", "SASL_PLAINTEXT");
        p.put(CommonClientConfigs.SECURITY_PROTOCOL_CONFIG, protocol);
        if (protocol.startsWith("SASL")) {
            String user = RulesEngineApp.env("PIP_KAFKA_USER", null);
            String pass = RulesEngineApp.env("PIP_KAFKA_PASSWORD", null);
            if (user.contains("\"") || pass.contains("\"")) throw new IllegalStateException("quotes not allowed in SASL credentials");
            p.put(SaslConfigs.SASL_MECHANISM, "SCRAM-SHA-256");
            p.put(SaslConfigs.SASL_JAAS_CONFIG, "org.apache.kafka.common.security.scram.ScramLoginModule required "
                    + "username=\"" + user + "\" password=\"" + pass + "\";");
        }
        return p;
    }

    private static void print(String outcome, RuleSet set, List<String> problems, boolean dryRun) {
        ObjectNode n = Json.MAPPER.createObjectNode();
        n.put("outcome", outcome);
        n.put("dryRun", dryRun);
        if (set != null) {
            n.put("sha256", set.sha256());
            ObjectNode v = n.putObject("versions");
            set.versions().forEach(v::put);
        }
        var arr = n.putArray("problems");
        problems.forEach(arr::add);
        System.out.println(n);
    }

    private static void fail(String message) {
        System.err.println("rules-publisher: " + message);
        System.exit(2);
    }
}
