/** Same-origin API client for the BFF. Session cookie + double-submit CSRF header; no tokens in JS. */

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly body?: unknown,
  ) {
    super(message);
  }
}

export function loginUrl(): string {
  return "/oauth2/authorization/keycloak";
}

export function readCookie(name: string, cookie: string = typeof document === "undefined" ? "" : document.cookie): string | undefined {
  for (const part of cookie.split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k === name) return decodeURIComponent(v.join("="));
  }
  return undefined;
}

export interface Response<T> {
  data: T;
  etag: string | null;
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<Response<T>> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  const method = (init.method ?? "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD") {
    const token = readCookie("XSRF-TOKEN");
    if (token) headers.set("X-XSRF-TOKEN", token);
    if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  }
  const res = await fetch(path, { ...init, headers, credentials: "same-origin" });
  if (res.status === 401) {
    window.location.assign(loginUrl());
    throw new ApiError(401, "unauthenticated", "Signing you in");
  }
  const text = await res.text();
  const body: unknown = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const p = (body ?? {}) as { code?: string; detail?: string };
    throw new ApiError(res.status, p.code ?? `http-${res.status}`, p.detail ?? res.statusText, body);
  }
  return { data: body as T, etag: res.headers.get("ETag") };
}

export const get = <T,>(path: string) => request<T>(path).then((r) => r.data);

export async function logout(): Promise<void> {
  const { data } = await request<{ logoutUrl: string }>("/logout", { method: "POST" });
  window.location.assign(data.logoutUrl);
}
