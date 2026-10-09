package com.pqt.eventcore.ingest;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.pqt.eventcore.config.PqtProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.io.IOException;
import java.nio.file.Path;
import java.time.Clock;

@Configuration
public class IngestBeans {
    @Bean
    Clock clock() {
        return Clock.systemUTC();
    }

    @Bean
    SchemaRegistry schemaRegistry(PqtProperties props, ObjectMapper mapper) throws IOException {
        return new SchemaRegistry(Path.of(props.paths().schemas()), mapper);
    }

    @Bean
    EventValidator eventValidator(SchemaRegistry registry, PqtProperties props, Clock clock) {
        return new EventValidator(registry, props.ingest(), clock);
    }

    @Bean
    SourcePolicy sourcePolicy(PqtProperties props) {
        return new SourcePolicy(props.ingest());
    }

    @Bean
    RateLimiter rateLimiter(PqtProperties props) {
        return new RateLimiter(props.ingest().rateLimitPerSecond(), props.ingest().rateLimitBurst(), System::nanoTime);
    }
}
