package com.pip.eventcore.config;

import jakarta.servlet.DispatcherType;
import org.springframework.boot.actuate.autoconfigure.security.servlet.EndpointRequest;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.annotation.Order;
import org.springframework.core.convert.converter.Converter;
import org.springframework.http.HttpStatus;
import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.oauth2.client.oidc.authentication.OidcIdTokenDecoderFactory;
import org.springframework.security.oauth2.client.oidc.authentication.OidcIdTokenValidator;
import org.springframework.security.oauth2.client.registration.ClientRegistration;
import org.springframework.security.oauth2.client.registration.ClientRegistrationRepository;
import org.springframework.security.oauth2.client.web.DefaultOAuth2AuthorizationRequestResolver;
import org.springframework.security.oauth2.client.web.OAuth2AuthorizationRequestCustomizers;
import org.springframework.security.oauth2.core.DelegatingOAuth2TokenValidator;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtDecoderFactory;
import org.springframework.security.oauth2.jwt.JwtIssuerValidator;
import org.springframework.security.oauth2.jwt.JwtValidators;
import org.springframework.security.oauth2.jwt.NimbusJwtDecoder;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationConverter;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.HttpStatusEntryPoint;
import org.springframework.security.web.authentication.www.BasicAuthenticationFilter;
import org.springframework.security.web.csrf.CookieCsrfTokenRepository;
import org.springframework.security.web.util.matcher.AntPathRequestMatcher;

import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Map;

/**
 * Three trust zones:
 *  1. /v1/**  machine ingest: OAuth2 bearer JWT (client credentials), role event-producer, stateless.
 *  2. /api/**, /oauth2/**, /login/**, /logout  console BFF: OIDC code flow + PKCE, server-side
 *     session, SPA CSRF, roles from the ID token. Tokens never reach the browser.
 *  3. everything else: denied.
 * Actuator listens on a separate management port that is not routed by the gateway.
 */
@Configuration
@EnableWebSecurity
@EnableMethodSecurity
public class SecurityConfig {

    @Bean
    @Order(0)
    SecurityFilterChain actuatorChain(HttpSecurity http) throws Exception {
        http.securityMatcher(EndpointRequest.toAnyEndpoint())
                .authorizeHttpRequests(a -> a.anyRequest().permitAll())
                .csrf(AbstractHttpConfigurer::disable)
                .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS));
        return http.build();
    }

    @Bean
    @Order(1)
    SecurityFilterChain ingestChain(HttpSecurity http, JwtDecoder ingestJwtDecoder) throws Exception {
        JwtAuthenticationConverter converter = new JwtAuthenticationConverter();
        converter.setJwtGrantedAuthoritiesConverter(realmRoles());
        http.securityMatcher("/v1/**")
                .authorizeHttpRequests(a -> a.anyRequest().hasRole("event-producer"))
                .oauth2ResourceServer(o -> o.jwt(j -> j.decoder(ingestJwtDecoder).jwtAuthenticationConverter(converter)))
                .csrf(AbstractHttpConfigurer::disable)
                .requestCache(AbstractHttpConfigurer::disable)
                .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS));
        return http.build();
    }

    @Bean
    @Order(2)
    SecurityFilterChain consoleChain(HttpSecurity http, ClientRegistrationRepository registrations,
                                     PipProperties props) throws Exception {
        DefaultOAuth2AuthorizationRequestResolver resolver =
                new DefaultOAuth2AuthorizationRequestResolver(registrations, "/oauth2/authorization");
        resolver.setAuthorizationRequestCustomizer(OAuth2AuthorizationRequestCustomizers.withPkce());

        CookieCsrfTokenRepository csrfRepository = CookieCsrfTokenRepository.withHttpOnlyFalse();
        csrfRepository.setCookieCustomizer(c -> c.path("/").sameSite("Strict"));

        http.securityMatcher("/api/**", "/oauth2/**", "/login/**", "/logout")
                .authorizeHttpRequests(a -> a
                        .requestMatchers("/oauth2/**", "/login/**").permitAll()
                        .anyRequest().authenticated())
                .oauth2Login(o -> o
                        .authorizationEndpoint(ae -> ae.authorizationRequestResolver(resolver))
                        .userInfoEndpoint(u -> u.userAuthoritiesMapper(new RealmRoleAuthoritiesMapper()))
                        .defaultSuccessUrl("/", true))
                .exceptionHandling(e -> e.defaultAuthenticationEntryPointFor(
                        new HttpStatusEntryPoint(HttpStatus.UNAUTHORIZED), new AntPathRequestMatcher("/api/**")))
                .csrf(c -> c.csrfTokenRepository(csrfRepository).csrfTokenRequestHandler(new SpaCsrfTokenRequestHandler()))
                .addFilterAfter(new CsrfCookieFilter(), BasicAuthenticationFilter.class)
                .logout(l -> l.logoutUrl("/logout").logoutSuccessHandler(new JsonLogoutSuccessHandler(props)))
                .sessionManagement(s -> s.sessionFixation(f -> f.changeSessionId()));
        return http.build();
    }

    @Bean
    @Order(3)
    SecurityFilterChain denyByDefault(HttpSecurity http) throws Exception {
        http.authorizeHttpRequests(a -> a
                        .dispatcherTypeMatchers(DispatcherType.ERROR).permitAll()
                        .requestMatchers("/error").permitAll()
                        .anyRequest().denyAll())
                .csrf(AbstractHttpConfigurer::disable)
                .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS));
        return http.build();
    }

    /** Ingest tokens: signature via internal JWKS, issuer must be the public issuer URL. */
    @Bean
    JwtDecoder ingestJwtDecoder(PipProperties props,
                                @Value("${spring.security.oauth2.resourceserver.jwt.jwk-set-uri}") String jwkSetUri) {
        NimbusJwtDecoder decoder = NimbusJwtDecoder.withJwkSetUri(jwkSetUri).build();
        decoder.setJwtValidator(JwtValidators.createDefaultWithIssuer(props.oidc().publicIssuer()));
        return decoder;
    }

    /** ID tokens: standard OIDC validation plus an explicit issuer check (no discovery document is used). */
    @Bean
    JwtDecoderFactory<ClientRegistration> idTokenDecoderFactory(PipProperties props) {
        OidcIdTokenDecoderFactory factory = new OidcIdTokenDecoderFactory();
        factory.setJwtValidatorFactory(reg -> new DelegatingOAuth2TokenValidator<>(
                new OidcIdTokenValidator(reg), new JwtIssuerValidator(props.oidc().publicIssuer())));
        return factory;
    }

    static Converter<Jwt, Collection<GrantedAuthority>> realmRoles() {
        return jwt -> {
            List<GrantedAuthority> out = new ArrayList<>();
            Object realmAccess = jwt.getClaims().get("realm_access");
            if (realmAccess instanceof Map<?, ?> m && m.get("roles") instanceof Collection<?> roles) {
                for (Object r : roles) out.add(new SimpleGrantedAuthority("ROLE_" + r));
            }
            return out;
        };
    }
}
