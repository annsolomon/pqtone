package com.pqt.eventcore.billing;

import org.springframework.jdbc.core.JdbcTemplate;

import java.util.regex.Pattern;

/**
 * The row-level-security scope of the billing tables (V7): the one store the current transaction may see and
 * write. Transaction-local (set_config(..., true)), so a pooled connection never carries it into the next
 * transaction; outside a transaction it would end with the statement and nothing would be visible.
 */
public final class StoreScope {
    static final Pattern STORE_ID = Pattern.compile("^[a-z0-9-]{1,64}$");

    private StoreScope() {
    }

    public static void set(JdbcTemplate jdbc, String storeId) {
        if (storeId == null || !STORE_ID.matcher(storeId).matches()) throw new IllegalArgumentException("invalid store id");
        jdbc.queryForObject("SELECT set_config('pqt.store_id', ?, true)", String.class, storeId);
    }
}
