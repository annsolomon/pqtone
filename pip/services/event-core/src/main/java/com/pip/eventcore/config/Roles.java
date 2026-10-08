package com.pip.eventcore.config;

import java.util.Collection;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/** Console role model: admin > reviewer > operator > viewer. */
public final class Roles {
    public static final String VIEWER = "viewer";
    public static final String OPERATOR = "operator";
    public static final String REVIEWER = "reviewer";
    public static final String ADMIN = "admin";
    private static final List<String> ORDER = List.of(VIEWER, OPERATOR, REVIEWER, ADMIN);

    private Roles() {
    }

    /** Expands each granted role to include every role below it. Unknown roles are ignored. */
    public static Set<String> expand(Collection<String> granted) {
        Set<String> out = new LinkedHashSet<>();
        if (granted == null) return out;
        for (String role : granted) {
            int idx = ORDER.indexOf(role);
            for (int i = 0; i <= idx; i++) out.add(ORDER.get(i));
        }
        return out;
    }
}
