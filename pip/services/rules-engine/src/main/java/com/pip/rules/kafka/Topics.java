package com.pip.rules.kafka;

/** Topic names, injected from configuration. */
public record Topics(String validated, String incidents, String shadow, String heartbeat) {
}
