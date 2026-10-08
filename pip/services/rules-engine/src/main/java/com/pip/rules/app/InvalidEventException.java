package com.pip.rules.app;

public final class InvalidEventException extends Exception {
    private static final long serialVersionUID = 1L;

    public InvalidEventException(String message) {
        super(message);
    }
}
