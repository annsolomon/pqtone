# syntax=docker/dockerfile:1.7
# Build context: repository root.
FROM node:22-alpine AS build
WORKDIR /app
COPY web/ops-console/package.json web/ops-console/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY web/ops-console/ ./
RUN npm run typecheck && npm test && npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY deploy/gateway/nginx.conf /etc/nginx/conf.d/default.conf
COPY deploy/gateway/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=6 CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
