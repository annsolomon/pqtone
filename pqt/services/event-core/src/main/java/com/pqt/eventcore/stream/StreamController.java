package com.pqt.eventcore.stream;

import org.springframework.http.MediaType;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

@RestController
public class StreamController {
    private final LiveHub hub;

    public StreamController(LiveHub hub) {
        this.hub = hub;
    }

    @GetMapping(path = "/api/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    @PreAuthorize("hasRole('viewer')")
    public SseEmitter stream() {
        return hub.subscribe();
    }
}
