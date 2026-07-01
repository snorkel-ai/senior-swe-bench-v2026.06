// @ts-nocheck
//
// The tests drive two pre-existing, stable interfaces:
//   1. `auth.api.<endpoint>(input)`, the typed server-side API surface.
//      A tiny task-local plugin exposes `c.context.baseURL` through a
//      probe endpoint so the resolved baseURL is observable without
//      instrumenting any internal module.
//   2. `auth.handler(request)`, the HTTP entry point, used as a
//      regression guard that handler-path resolution still works.
//
// Tests do NOT reference the fix's new internal symbols or primitives
// by name, since a valid alternative may name or place them
// differently, and assert only on `baseURL` (the externally
// observable contract) rather than other `c.context` fields.
//
// `getTestInstance` is avoided because it auto-includes `bearer()`,
// whose `before` hook reads
// `context.request?.headers.get("authorization")` and crashes on a
// duck-typed Node `IncomingMessage` (plain-object `headers`); that
// crash trips the request before the new resolution code runs, making
// the duck-type test unobservable. Calling `betterAuth()` directly
// with only the probe plugin keeps the hook chain clean.

import { describe, expect, it } from "vitest";
import { betterAuth } from "better-auth";
import { createAuthEndpoint } from "@better-auth/core/api";

// The probe endpoint returns the resolved baseURL the handler sees.
const baseURLProbe = () => ({
  id: "base-url-probe",
  endpoints: {
    readBaseURL: createAuthEndpoint(
      "/base-url-probe/read",
      { method: "GET" },
      async (c) => ({ baseURL: c.context.baseURL }),
    ),
  },
});

const SECRET =
  "better-auth-secret-that-is-long-enough-for-validation-test";

// Only the probe plugin is attached; no DB is needed because the probe
// just reads `c.context.baseURL`. `logger.level: "error"` suppresses
// the init-time warning that fires for dynamic configs.
const buildAuth = (baseURL: any) =>
  betterAuth({
    secret: SECRET,
    baseURL,
    plugins: [baseURLProbe()],
    logger: { level: "error" },
  } as any);

describe("dynamic baseURL resolution", () => {
  // Pre-fix, direct auth.api.* calls never resolve the dynamic baseURL
  // (the endpoint sees the empty default); post-fix the per-call host
  // is honored.
  it("direct_api_call_resolves_dynamic_baseurl_from_host", async () => {
    const auth = buildAuth({
      allowedHosts: ["example.com"],
      protocol: "https",
    });

    const res = await auth.api.readBaseURL({
      headers: new Headers({ host: "example.com" }),
    });
    expect(res.baseURL).toBe("https://example.com/api/auth");
  });

  // Static (string) baseURL configs must stay unaffected by the host
  // header on direct API calls.
  it("static_baseurl_unchanged_for_direct_api_call", async () => {
    const auth = buildAuth("https://static.example.com");

    const res = await auth.api.readBaseURL({
      headers: new Headers({ host: "other.example.com" }),
    });
    expect(res.baseURL).toBe("https://static.example.com/api/auth");
  });

  // Two concurrent direct calls with different host headers must each
  // see their own resolved baseURL; the shared raw context's baseURL
  // must remain the dynamic-config default of "". Catches
  // implementations that mutate the shared context.
  it("concurrent_direct_api_calls_isolate_per_request", async () => {
    const auth = buildAuth({
      allowedHosts: ["tenant-a.example.com", "tenant-b.example.com"],
      protocol: "https",
    });

    const [resA, resB] = await Promise.all([
      auth.api.readBaseURL({
        headers: new Headers({ host: "tenant-a.example.com" }),
      }),
      auth.api.readBaseURL({
        headers: new Headers({ host: "tenant-b.example.com" }),
      }),
    ]);
    expect(resA.baseURL).toBe("https://tenant-a.example.com/api/auth");
    expect(resB.baseURL).toBe("https://tenant-b.example.com/api/auth");

    // Shared context untouched — concurrent tenants don't see each
    // other's resolved URL bleeding through on later calls.
    const sharedCtx = await auth.$context;
    expect(sharedCtx.baseURL).toBe("");
  });

  // A duck-typed Node `http.IncomingMessage` (plain-object headers,
  // `socket: {}`) passed as `request` alongside a real Web Headers
  // instance must NOT crash inside header-parsing code. The resolved
  // baseURL is derived from the explicit Headers source. Catches
  // implementations that accept any object with a `.headers` field as
  // a Fetch Request and then call `.get(...)` on a plain JS object.
  it("direct_api_call_handles_node_incoming_message_shape", async () => {
    const auth = buildAuth({
      allowedHosts: ["example.com"],
      protocol: "https",
    });

    const fakeIncomingMessage = {
      url: "/base-url-probe/read",
      method: "GET",
      headers: { host: "example.com" }, // plain object, NOT Web Headers
      socket: {},
    };
    const res = await auth.api.readBaseURL({
      request: fakeIncomingMessage as unknown as Request,
      headers: new Headers({ host: "example.com" }),
    });
    expect(res.baseURL).toBe("https://example.com/api/auth");
  });

  // Direct API calls supplying only an `x-forwarded-host` header (no
  // `host`) still trigger dynamic resolution, mirroring the
  // handler-side proxy support. Catches implementations that only
  // check the `host` header on the direct path.
  it("direct_api_call_uses_x_forwarded_host_header", async () => {
    const auth = buildAuth({
      allowedHosts: ["example.com"],
      protocol: "https",
    });

    const res = await auth.api.readBaseURL({
      headers: new Headers({ "x-forwarded-host": "example.com" }),
    });
    expect(res.baseURL).toBe("https://example.com/api/auth");
  });

  // Regression guard: handler-side dynamic resolution must keep
  // working unchanged. `auth.handler(request)` produces the same
  // per-request baseURL whether the underlying clone happens inline
  // or via an extracted helper.
  it("handler_dynamic_baseurl_regression_via_request_url", async () => {
    const auth = buildAuth({
      allowedHosts: ["example.com"],
      protocol: "https",
    });

    const resp = await auth.handler(
      new Request("https://example.com/api/auth/base-url-probe/read"),
    );
    expect(resp.status).toBe(200);
    const body = await resp.json();
    expect(body.baseURL).toBe("https://example.com/api/auth");
  });

  // When the dynamic config provides a `fallback` URL and the direct
  // call carries neither a `request` nor host-bearing headers, the
  // endpoint sees the fallback baseURL (rather than the empty
  // default). Catches fixes that ignore `config.fallback` on the
  // direct path.
  it("direct_api_call_uses_dynamic_fallback_when_no_source", async () => {
    const auth = buildAuth({
      allowedHosts: ["example.com"],
      protocol: "https",
      fallback: "https://fallback.example.com",
    });

    const res = await auth.api.readBaseURL({});
    expect(res.baseURL).toBe("https://fallback.example.com/api/auth");
  });
});
