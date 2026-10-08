package com.pip.eventcore.ingest;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.pip.eventcore.config.PipProperties;
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
    SchemaRegistry schemaRegistry(PipProperties props, ObjectMapper mapper) throws IOException {
        return new SchemaRegistry(Path.of(props.paths().schemas()), mapper);
    }

    @Bean
    EventValidator eventValidator(SchemaRegistry registry, PipProperties props, Clock clock) {
        return new EventValidator(registry, props.ingest(), clock);
    }

    @Bean
    SourcePolicy sourcePolicy(PipProperties props) {
        return new SourcePolicy(props.ingest());
    }

    @Bean
    RateLimiter rateLimiter(PipProperties props) {
        return new RateLimiter(props.ingest().rateLimitPerSecond(), props.ingest().rateLimitBurst(), System::nanoTime);
    }
}
