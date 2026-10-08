package com.pip.eventcore.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.http.MediaType;
import org.springframework.security.core.Authentication;
import org.springframework.security.oauth2.core.oidc.user.OidcUser;
import org.springframework.security.web.authentication.logout.LogoutSuccessHandler;
import org.springframework.web.util.UriComponentsBuilder;

import java.io.IOException;
import java.util.Map;

/**
 * The SPA logs out with a CSRF-protected POST; we answer with the IdP end-session URL
 * (RP-initiated logout) and let the browser navigate there.
 */
public final class JsonLogoutSuccessHandler implements LogoutSuccessHandler {
    private final PipProperties props;
    private final ObjectMapper mapper = new ObjectMapper();

    public JsonLogoutSuccessHandler(PipProperties props) {
        this.props = props;
    }

    @Override
    public void onLogoutSuccess(HttpServletRequest request, HttpServletResponse response, Authentication auth)
            throws IOException {
        UriComponentsBuilder b = UriComponentsBuilder.fromUriString(props.oidc().endSessionUri())
                .queryParam("client_id", props.oidc().clientId())
                .queryParam("post_logout_redirect_uri", props.oidc().postLogoutRedirectUri());
        if (auth != null && auth.getPrincipal() instanceof OidcUser user) {
            b.queryParam("id_token_hint", user.getIdToken().getTokenValue());
        }
        response.setStatus(200);
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        mapper.writeValue(response.getOutputStream(), Map.of("logoutUrl", b.encode().build().toUriString()));
    }
}
