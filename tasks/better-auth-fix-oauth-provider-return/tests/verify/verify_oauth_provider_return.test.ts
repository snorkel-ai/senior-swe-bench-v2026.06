// @ts-nocheck
//
// Behavioural verifier for the OAuth provider RFC error-envelope fix.
//
// Imports ONLY public package exports, never `packages/oauth-provider/src/*`
// internals: those are implementation details a valid alternative may
// rename, inline, or omit. Assertions are limited to standardised signals
// (HTTP status, the RFC top-level `error` code, the query-vs-fragment
// channel of the authorize redirect, the Location host/prefix, and the
// presence of `error_description`/`state`/`iss`) so any compliant
// implementation passes. The one substring assertion `/response_type/` is
// fair: RFC 6749 §3.1 names the offending parameter, so any compliant
// message mentions it.

import { generateRandomString } from "better-auth/crypto";
import {
  createAuthorizationCodeRequest,
  createAuthorizationURL,
} from "better-auth/oauth2";
import { jwt } from "better-auth/plugins/jwt";
import { getTestInstance } from "better-auth/test";
import { createAuthClient } from "better-auth/client";
import { beforeAll, describe, expect, it } from "vitest";
import { oauthProvider } from "@better-auth/oauth-provider";
import { oauthProviderClient } from "@better-auth/oauth-provider/client";

const AUTH_BASE = "http://localhost:3000";
const PROVIDER_ID = "test";
const REDIRECT_URI = `http://localhost:5000/api/auth/oauth2/callback/${PROVIDER_ID}`;

describe("oauth-provider RFC error-envelope compliance", async () => {
  const { auth, signInWithTestUser, customFetchImpl } = await getTestInstance({
    baseURL: AUTH_BASE,
    plugins: [
      jwt({ jwt: { issuer: AUTH_BASE } }),
      oauthProvider({
        loginPage: "/login",
        consentPage: "/consent",
        // Enables the dynamic client registration (RFC 7591) endpoint so
        // the /oauth2/register happy path can succeed; validation-failure
        // codes are unaffected by this toggle.
        allowDynamicClientRegistration: true,
        silenceWarnings: {
          oauthAuthServerConfig: true,
          openidConfig: true,
        },
      }),
    ],
  });

  const { headers } = await signInWithTestUser();
  const client = createAuthClient({
    plugins: [oauthProviderClient()],
    baseURL: AUTH_BASE,
    fetchOptions: { customFetchImpl, headers },
  });

  // A registered, enabled confidential client with a known redirect_uri.
  // Used as the "trusted RP" for the authorize-redirect cases and to mint
  // real tokens for the token_type_hint / happy-path cases.
  let oauthClient: {
    client_id?: string;
    client_secret?: string;
    redirect_uris?: string[];
  } | null = null;

  beforeAll(async () => {
    oauthClient = await auth.api.adminCreateOAuthClient({
      headers,
      body: {
        redirect_uris: [REDIRECT_URI],
        scope: "openid profile email offline_access",
        skip_consent: true,
      },
    });
    if (!oauthClient?.client_id || !oauthClient?.client_secret) {
      throw new Error("adminCreateOAuthClient did not return credentials");
    }
  });

  type Envelope = {
    error?: string;
    error_description?: string;
    active?: boolean;
    [k: string]: unknown;
  };

  // Capture status + parsed JSON body for any (success or error) response.
  // `onResponse` fires for every response regardless of status, so this
  // works for both 200 and 400.
  async function captureJson(
    path: string,
    init: Record<string, unknown>,
  ): Promise<{ status: number; body: Envelope | null }> {
    let status = 0;
    let body: Envelope | null = null;
    await client.$fetch(path, {
      ...init,
      onResponse: async (context: any) => {
        status = context.response.status;
        try {
          body = (await context.response.clone().json()) as Envelope;
        } catch {
          body = null;
        }
      },
    });
    return { status, body };
  }

  // Capture the redirect status + Location header without following it.
  async function captureRedirect(
    path: string,
  ): Promise<{ status: number; location: string | null }> {
    let status = 0;
    let location: string | null = null;
    await client.$fetch(path, {
      method: "GET",
      redirect: "manual",
      onResponse: async (context: any) => {
        status = context.response.status;
        location = context.response.headers.get("location");
      },
    });
    return { status, location };
  }

  function postForm(
    path: string,
    body: Record<string, string>,
    extraHeaders: Record<string, string> = {},
  ) {
    return captureJson(path, {
      method: "POST",
      body,
      headers: {
        "content-type": "application/x-www-form-urlencoded",
        accept: "application/json",
        ...extraHeaders,
      },
    });
  }

  function postJson(path: string, body: Record<string, unknown>) {
    return captureJson(path, { method: "POST", body });
  }

  // Run a full, valid authorization-code authorize and return the RP
  // callback redirect Location (which carries `?code=...`). Used to mint
  // real tokens and to assert the authorize happy path.
  async function authorizeForCode(
    scopes: string[],
    state = "123",
  ): Promise<{ location: string; codeVerifier: string }> {
    const codeVerifier = generateRandomString(32);
    const url = await createAuthorizationURL({
      id: PROVIDER_ID,
      options: {
        clientId: oauthClient!.client_id!,
        clientSecret: oauthClient!.client_secret!,
        redirectURI: REDIRECT_URI,
      },
      redirectURI: "",
      authorizationEndpoint: `${AUTH_BASE}/api/auth/oauth2/authorize`,
      state,
      scopes,
      codeVerifier,
    });
    const { location } = await captureRedirect(url.toString());
    return { location: location || "", codeVerifier };
  }

  // Mint a real access token via the full code flow.
  async function mintTokens(scopes: string[] = ["openid", "profile", "email"]) {
    const { location, codeVerifier } = await authorizeForCode(scopes);
    const code = new URL(location).searchParams.get("code");
    if (!code) {
      throw new Error(`authorize did not yield a code: ${location}`);
    }
    const { body, headers: reqHeaders } = createAuthorizationCodeRequest({
      code,
      codeVerifier,
      redirectURI: REDIRECT_URI,
      options: {
        clientId: oauthClient!.client_id!,
        clientSecret: oauthClient!.client_secret!,
        redirectURI: REDIRECT_URI,
      },
    });
    const tokens = await client.$fetch<{
      access_token?: string;
      [k: string]: unknown;
    }>("/oauth2/token", { method: "POST", body, headers: reqHeaders });
    return tokens;
  }

  it("token_missing_grant_type_invalid_request", async () => {
    const { status, body } = await postForm("/oauth2/token", {});
    expect(status).toBe(400);
    expect(body?.error).toBe("invalid_request");
    expect(typeof body?.error_description).toBe("string");
    expect((body?.error_description as string).length).toBeGreaterThan(0);
  });

  it("token_unsupported_grant_type", async () => {
    const { status, body } = await postForm("/oauth2/token", {
      grant_type: "password",
    });
    expect(status).toBe(400);
    expect(body?.error).toBe("unsupported_grant_type");
  });

  it("revoke_missing_token_invalid_request", async () => {
    const { status, body } = await postForm("/oauth2/revoke", {});
    expect(status).toBe(400);
    expect(body?.error).toBe("invalid_request");
    expect(typeof body?.error_description).toBe("string");
    expect((body?.error_description as string).length).toBeGreaterThan(0);
  });

  it("introspect_missing_token_invalid_request", async () => {
    const { status, body } = await postForm("/oauth2/introspect", {});
    expect(status).toBe(400);
    expect(body?.error).toBe("invalid_request");
  });

  it("register_missing_redirect_uris_invalid_redirect_uri", async () => {
    const { status, body } = await postJson("/oauth2/register", {});
    expect(status).toBe(400);
    expect(body?.error).toBe("invalid_redirect_uri");
  });

  it("register_unsupported_auth_method_invalid_client_metadata", async () => {
    const { status, body } = await postJson("/oauth2/register", {
      redirect_uris: [REDIRECT_URI],
      token_endpoint_auth_method: "not_a_real_method",
    });
    expect(status).toBe(400);
    expect(body?.error).toBe("invalid_client_metadata");
  });

  it("end_session_missing_hint_invalid_request", async () => {
    const { status, body } = await captureJson("/oauth2/end-session", {
      method: "GET",
    });
    expect(status).toBe(400);
    expect(body?.error).toBe("invalid_request");
  });

  it("authorize_error_redirects_to_rp_with_fragment_envelope", async () => {
    const state = "opaque-state-abc";
    const qs = new URLSearchParams({
      client_id: oauthClient!.client_id!,
      redirect_uri: REDIRECT_URI,
      response_type: "token", // unsupported value (implicit) → fragment
      state,
    }).toString();
    const { status, location } = await captureRedirect(
      `/oauth2/authorize?${qs}`,
    );
    expect(status).toBeGreaterThanOrEqual(300);
    expect(status).toBeLessThan(400);
    expect(location).toBeTruthy();
    // Delivered to the registered RP redirect_uri.
    expect((location as string).startsWith(REDIRECT_URI)).toBe(true);
    const url = new URL(location as string);
    // Implicit/hybrid response type → params in the fragment (OIDC §5).
    expect(url.hash).toBeTruthy();
    const params = new URLSearchParams((url.hash as string).slice(1));
    expect(params.get("error")).toBe("unsupported_response_type");
    expect(params.get("error_description")).toBeTruthy();
    expect(params.get("state")).toBe(state);
    expect(params.get("iss")).toBeTruthy();
  });

  it("authorize_response_mode_query_overrides", async () => {
    const qs = new URLSearchParams({
      client_id: oauthClient!.client_id!,
      redirect_uri: REDIRECT_URI,
      response_type: "token",
      response_mode: "query", // overrides the fragment default
      state: "s",
    }).toString();
    const { location } = await captureRedirect(`/oauth2/authorize?${qs}`);
    expect(location).toBeTruthy();
    const url = new URL(location as string);
    expect(url.hash).toBe("");
    expect(url.searchParams.get("error")).toBe("unsupported_response_type");
  });

  it("authorize_missing_client_id_server_error_page", async () => {
    const qs = new URLSearchParams({
      redirect_uri: REDIRECT_URI,
      response_type: "code",
      state: "s",
    }).toString();
    const { status, location } = await captureRedirect(
      `/oauth2/authorize?${qs}`,
    );
    expect(status).toBeGreaterThanOrEqual(300);
    expect(status).toBeLessThan(400);
    expect(location).toBeTruthy();
    // No trusted client → must NOT redirect to the requested redirect_uri.
    expect((location as string).startsWith(REDIRECT_URI)).toBe(false);
    const url = new URL(location as string);
    expect(url.searchParams.get("error")).toBe("invalid_request");
    expect(url.searchParams.get("error_description")).toBeTruthy();
  });

  it("authorize_unregistered_redirect_uri_server_error_page", async () => {
    const qs = new URLSearchParams({
      client_id: oauthClient!.client_id!,
      redirect_uri: "http://evil.example.com/callback",
      response_type: "token",
      state: "s",
    }).toString();
    const { status, location } = await captureRedirect(
      `/oauth2/authorize?${qs}`,
    );
    expect(status).toBeGreaterThanOrEqual(300);
    expect(status).toBeLessThan(400);
    expect(location).toBeTruthy();
    // Open-redirect guard: the attacker-supplied URI is never a target.
    expect((location as string).startsWith("http://evil.example.com")).toBe(
      false,
    );
  });

  it("authorize_duplicated_response_type_invalid_request", async () => {
    const qs = new URLSearchParams([
      ["client_id", oauthClient!.client_id!],
      ["redirect_uri", REDIRECT_URI],
      ["response_type", "code"],
      ["response_type", "token"],
      ["state", "s"],
    ]).toString();
    const { status, location } = await captureRedirect(
      `/oauth2/authorize?${qs}`,
    );
    expect(status).toBeGreaterThanOrEqual(300);
    expect(status).toBeLessThan(400);
    expect(location).toBeTruthy();
    const url = new URL(location as string);
    // A duplicated scalar param is invalid_request (RFC 6749 §3.1), NOT
    // unsupported_response_type — even though response_type HAS an
    // unsupported-value mapping. Duplicated/ambiguous → query channel.
    expect(url.searchParams.get("error")).toBe("invalid_request");
    expect(url.searchParams.get("error_description")).toMatch(/response_type/);
  });

  it("revoke_ignores_unknown_token_type_hint", async () => {
    const tokens = await mintTokens();
    const accessToken = tokens.data?.access_token as string;
    expect(typeof accessToken).toBe("string");

    // Unknown hint must be ignored (RFC 7009 §2.2.1): the server falls back
    // to searching across the token types it supports and the request still
    // succeeds (200). Pre-fix the strict enum schema rejects "id_token" with
    // a 400 + error "unsupported_token_type" — the code RFC 7009 reserves for
    // the token TYPE, not the hint. The fix must not surface that code here.
    const { status, body } = await postForm("/oauth2/revoke", {
      client_id: oauthClient!.client_id!,
      client_secret: oauthClient!.client_secret!,
      token: accessToken,
      token_type_hint: "id_token",
    });
    expect(status).toBe(200);
    expect(body?.error).not.toBe("unsupported_token_type");
  });

  it("introspect_ignores_unknown_token_type_hint", async () => {
    const tokens = await mintTokens();
    const accessToken = tokens.data?.access_token as string;
    expect(typeof accessToken).toBe("string");

    const { status, body } = await postForm("/oauth2/introspect", {
      client_id: oauthClient!.client_id!,
      client_secret: oauthClient!.client_secret!,
      token: accessToken,
      token_type_hint: "id_token", // unknown hint → must be ignored
    });
    expect(status).toBe(200);
    expect(body?.active).toBe(true);
  });

  // Regression guards: these happy paths pass both pre-fix and post-fix.
  it("token_happy_path_issues_access_token", async () => {
    const tokens = await mintTokens(["openid"]);
    expect(typeof tokens.data?.access_token).toBe("string");
    expect((tokens.data?.access_token as string).length).toBeGreaterThan(0);
  });

  it("authorize_happy_path_issues_code", async () => {
    const { location } = await authorizeForCode(["openid", "profile"]);
    expect(location).toContain(REDIRECT_URI);
    const url = new URL(location);
    expect(url.searchParams.get("code")).toBeTruthy();
    expect(url.searchParams.get("error")).toBeFalsy();
  });

  it("register_happy_path_returns_client_id", async () => {
    const { status, body } = await postJson("/oauth2/register", {
      redirect_uris: ["http://localhost:5000/api/auth/oauth2/callback/other"],
    });
    expect(status).toBeGreaterThanOrEqual(200);
    expect(status).toBeLessThan(300);
    expect(typeof body?.client_id).toBe("string");
    expect((body?.client_id as string).length).toBeGreaterThan(0);
  });

  it("revoke_valid_hint_succeeds", async () => {
    const tokens = await mintTokens();
    const accessToken = tokens.data?.access_token as string;
    expect(typeof accessToken).toBe("string");
    const { status } = await postForm("/oauth2/revoke", {
      client_id: oauthClient!.client_id!,
      client_secret: oauthClient!.client_secret!,
      token: accessToken,
      token_type_hint: "access_token",
    });
    expect(status).toBe(200);
  });
});
