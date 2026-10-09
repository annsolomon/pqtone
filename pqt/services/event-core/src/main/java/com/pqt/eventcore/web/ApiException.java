package com.pqt.eventcore.web;

import org.springframework.http.HttpStatus;

import java.util.Map;

/** Maps to an RFC 9457 problem response. */
public class ApiException extends RuntimeException {
    private static final long serialVersionUID = 1L;
    private final HttpStatus status;
    private final String code;
    private final transient Map<String, Object> extras;

    public ApiException(HttpStatus status, String code, String detail) {
        this(status, code, detail, Map.of());
    }

    public ApiException(HttpStatus status, String code, String detail, Map<String, Object> extras) {
        super(detail);
        this.status = status;
        this.code = code;
        this.extras = extras;
    }

    public HttpStatus status() {
        return status;
    }

    public String code() {
        return code;
    }

    public Map<String, Object> extras() {
        return extras;
    }
}
