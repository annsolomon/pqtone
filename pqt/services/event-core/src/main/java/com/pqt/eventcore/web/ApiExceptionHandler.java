package com.pqt.eventcore.web;

import com.pqt.eventcore.ingest.InvalidEventException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ProblemDetail;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import java.net.URI;

@RestControllerAdvice
public class ApiExceptionHandler {
    private static final String TYPE_BASE = "https://errors.pqt.local/";

    @ExceptionHandler(ApiException.class)
    ResponseEntity<ProblemDetail> api(ApiException ex) {
        ProblemDetail pd = ProblemDetail.forStatusAndDetail(ex.status(), ex.getMessage());
        pd.setType(URI.create(TYPE_BASE + ex.code()));
        pd.setTitle(ex.status().getReasonPhrase());
        pd.setProperty("code", ex.code());
        ex.extras().forEach(pd::setProperty);
        ResponseEntity.BodyBuilder b = ResponseEntity.status(ex.status());
        Object retry = ex.extras().get("retryAfterSeconds");
        if (retry != null) b.header("Retry-After", String.valueOf(retry));
        return b.body(pd);
    }

    @ExceptionHandler(InvalidEventException.class)
    ResponseEntity<ProblemDetail> invalid(InvalidEventException ex) {
        ProblemDetail pd = ProblemDetail.forStatusAndDetail(HttpStatus.BAD_REQUEST, "event violates the contract");
        pd.setType(URI.create(TYPE_BASE + "invalid-event"));
        pd.setTitle("Invalid event");
        pd.setProperty("code", "invalid-event");
        pd.setProperty("errors", ex.errors());
        return ResponseEntity.badRequest().body(pd);
    }
}
