package com.pip.eventcore.ingest;

import java.util.List;

/** Event failed the contract. Carries every problem found, not just the first. */
public class InvalidEventException extends RuntimeException {
    private static final long serialVersionUID = 1L;
    private final transient List<String> errors;

    public InvalidEventException(List<String> errors) {
        super(String.join("; ", errors));
        this.errors = List.copyOf(errors);
    }

    public List<String> errors() {
        return errors;
    }
}
