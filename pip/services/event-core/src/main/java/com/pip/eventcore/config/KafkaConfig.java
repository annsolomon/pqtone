package com.pip.eventcore.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.listener.CommonErrorHandler;
import org.springframework.kafka.listener.DefaultErrorHandler;
import org.springframework.util.backoff.ExponentialBackOff;

@Configuration
public class KafkaConfig {
    /**
     * Infrastructure failures (database down, DLQ unavailable) are retried indefinitely with
     * capped exponential back-off: the partition pauses rather than skipping records. Contract
     * violations never reach this handler; they are routed to the DLQ by the listener itself.
     */
    @Bean
    CommonErrorHandler kafkaErrorHandler() {
        ExponentialBackOff backOff = new ExponentialBackOff(500L, 2.0);
        backOff.setMaxInterval(30_000L);
        return new DefaultErrorHandler(backOff);
    }
}
