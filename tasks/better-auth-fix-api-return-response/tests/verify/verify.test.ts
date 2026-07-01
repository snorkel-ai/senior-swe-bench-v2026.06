// @ts-nocheck
//
// Tests drive pre-existing stable interfaces only, so the fix is
// observable through the calling convention alone:
//   - `auth.api.ok` / `auth.api.signInEmail` (typed server-side API
//     from getTestInstance)
//   - `toAuthEndpoints({...}, init({hooks: {before}}))` direct-use
//     pattern (matches the repo's own to-auth-endpoints.test.ts)
//
// The happy-path probe targets `/api/auth/ok` (non-OAuth) so a
// path-allow-list fix fails rather than passing.

import { describe, expect, it } from "vitest";
import {
	createAuthEndpoint,
	createAuthMiddleware,
} from "@better-auth/core/api";
import { init } from "../context/init";
import { getTestInstance } from "../test-utils/test-instance";
import { toAuthEndpoints } from "./to-auth-endpoints";

describe("HTTP request contexts return Response (PR #7521)", () => {
	// FAIL-TO-PASS, basic_functionality.
	// Final-response branch: the typed-API call must materialise a real
	// Response when a Request is in the input context, even for a
	// non-OAuth route — otherwise a path-allow-list fix would slip
	// through.
	it("auth.api endpoint with request context returns a Response (final-response branch)", async () => {
		const { auth } = await getTestInstance();
		const result: any = await auth.api.ok({
			request: new Request("http://localhost:3000/api/auth/ok", {
				method: "GET",
			}),
		} as any);
		expect(result).toBeInstanceOf(Response);
		expect((result as Response).status).toBe(200);
		const body = await (result as Response).json();
		expect(body).toEqual({ ok: true });
	});

	// FAIL-TO-PASS, senior-engineer nuance.
	// APIError branch: when the handler resolves to an APIError (here,
	// invalid credentials -> UNAUTHORIZED), the dispatcher must wrap it
	// in a 4xx Response instead of throwing — partial fixes that touch
	// only the happy-path branch leave this case throwing.
	it("auth.api endpoint with request context surfaces APIError as a 4xx Response (APIError branch)", async () => {
		const { auth } = await getTestInstance();
		let threw: any = null;
		let resolved: any = null;
		try {
			resolved = await auth.api.signInEmail({
				request: new Request(
					"http://localhost:3000/api/auth/sign-in/email",
					{ method: "POST" },
				),
				body: {
					email: "nobody@example.com",
					password: "wrongpassword",
				},
			} as any);
		} catch (e) {
			threw = e;
		}
		expect(threw).toBeNull();
		expect(resolved).toBeInstanceOf(Response);
		const status = (resolved as Response).status;
		expect(status).toBeGreaterThanOrEqual(400);
		expect(status).toBeLessThan(500);
	});

	// FAIL-TO-PASS, senior-engineer nuance.
	// Before-hook short-circuit branch: when a registered before-hook
	// short-circuits the handler with a non-context value, that value
	// must be wrapped in a Response when the caller has an HTTP request
	// in hand. Catches partial fixes that only changed the happy-path
	// branch.
	it("before-hook short-circuit + request context wraps the hook value in a Response (before-hook branch)", async () => {
		const endpoints = {
			hello: createAuthEndpoint(
				"/hello",
				{ method: "POST" },
				async () => ({ from: "handler" }),
			),
		};
		const authContext = init({
			hooks: {
				before: createAuthMiddleware(async (c: any) => {
					if (c.path === "/hello") {
						return { fromHook: true };
					}
				}),
			},
		});
		const api = toAuthEndpoints(endpoints, authContext);
		const result: any = await api.hello({
			request: new Request("http://localhost:3000/hello", {
				method: "POST",
			}),
		} as any);
		expect(result).toBeInstanceOf(Response);
		const body = await (result as Response).json();
		expect(body).toMatchObject({ fromHook: true });
	});

	// PASS-TO-PASS, basic_functionality.
	// Regression guard: typed-API callers that do NOT supply a Request
	// must continue to receive the unwrapped business value. Catches
	// over-corrections that wrap every auth.api.* result in a Response.
	it("auth.api endpoint without request context returns the plain value (regression guard)", async () => {
		const { auth } = await getTestInstance();
		const result: any = await auth.api.ok();
		expect(result).not.toBeInstanceOf(Response);
		expect(result).toMatchObject({ ok: true });
	});
});
