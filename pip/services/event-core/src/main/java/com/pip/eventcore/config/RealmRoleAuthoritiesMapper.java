package com.pip.eventcore.config;

import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.authority.mapping.GrantedAuthoritiesMapper;
import org.springframework.security.oauth2.core.oidc.user.OidcUserAuthority;

import java.util.Collection;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/** Maps the Keycloak "roles" ID-token claim to ROLE_* authorities with the role hierarchy applied. */
public final class RealmRoleAuthoritiesMapper implements GrantedAuthoritiesMapper {
    @Override
    public Collection<? extends GrantedAuthority> mapAuthorities(Collection<? extends GrantedAuthority> authorities) {
        Set<GrantedAuthority> out = new HashSet<>(authorities);
        for (GrantedAuthority a : authorities) {
            if (a instanceof OidcUserAuthority oidc) {
                List<String> roles = oidc.getIdToken().getClaimAsStringList("roles");
                for (String role : Roles.expand(roles)) out.add(new SimpleGrantedAuthority("ROLE_" + role));
            }
        }
        return out;
    }
}
