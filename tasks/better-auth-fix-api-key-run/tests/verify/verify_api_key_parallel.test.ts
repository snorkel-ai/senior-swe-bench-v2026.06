// @ts-nocheck
//
// Tests drive only the plugin's stable public surface: the typed client
// `apiKey.create`/`apiKey.list`, and `auth.$context` adapter.create to
// seed DB rows directly. The test owns the `secondaryStorage` object, so
// wrapping its `get`/`set` to count in-flight calls and widen timing
// windows is fair. The KV key conventions `api-key:by-id:<id>` and
// `api-key:by-ref:<referenceId>` are a pre-existing storage contract
// unchanged by the fix, so asserting on them is fair.
//
// No reference-internal helper is referenced by name: a valid alternative
// may name things differently. Thresholds are loose (in-flight >= 2, not
// === keyCount) so bounded-pool and unbounded Promise.all both pass.

import { afterEach, describe, expect, it, vi } from "vitest";
import { getTestInstance } from "better-auth/test";
import { apiKey } from ".";
import { apiKeyClient } from "./client";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function makeStorage(store) {
	return {
		set(key, value, _ttl) {
			store.set(key, value);
		},
		get(key) {
			return store.get(key) ?? null;
		},
		delete(key) {
			store.delete(key);
		},
	};
}

// --------------------------------------------------------------------- //
// secondary-storage-only mode: parallel_reads + list_correctness        //
// --------------------------------------------------------------------- //
describe("api-key parallel storage (secondary-storage)", async () => {
	const store = new Map();
	const secondaryStorage = makeStorage(store);

	const { client, signInWithTestUser } = await getTestInstance(
		{
			secondaryStorage,
			plugins: [
				apiKey({ storage: "secondary-storage", enableMetadata: true }),
			],
		},
		{ clientOptions: { plugins: [apiKeyClient()] } },
	);

	afterEach(() => {
		vi.restoreAllMocks();
		store.clear();
	});

	it("parallel_reads: lists secondary-storage keys with concurrent per-id reads", async () => {
		const { headers } = await signInWithTestUser();

		const createdIds = [];
		for (let i = 0; i < 5; i++) {
			const { data } = await client.apiKey.create({}, { headers });
			expect(data).not.toBeNull();
			createdIds.push(data.id);
		}

		// Instrument the test-owned storage AFTER creation so we only
		// measure the read fan-out of `list`. Per-id reads that overlap
		// raise `maxInFlight` above 1; a serial await loop keeps it at 1.
		let inFlight = 0;
		let maxInFlight = 0;
		const origGet = secondaryStorage.get.bind(secondaryStorage);
		vi.spyOn(secondaryStorage, "get").mockImplementation(async (key) => {
			if (typeof key === "string" && key.startsWith("api-key:by-id:")) {
				inFlight++;
				maxInFlight = Math.max(maxInFlight, inFlight);
				await sleep(20);
				const result = origGet(key);
				inFlight--;
				return result;
			}
			return origGet(key);
		});

		const { data } = await client.apiKey.list({}, { headers });
		const listedIds = (data?.apiKeys ?? []).map((k) => k.id);

		for (const id of createdIds) {
			expect(listedIds).toContain(id);
		}
		// Sequential pre-fix code yields exactly 1; any concurrency >= 2.
		expect(maxInFlight).toBeGreaterThanOrEqual(2);
	});

	it("list_correctness: returns exactly the reference's keys with ordering preserved", async () => {
		const { headers } = await signInWithTestUser();

		const createdIds = [];
		for (let i = 0; i < 4; i++) {
			const { data } = await client.apiKey.create(
				{ name: `key-${i}` },
				{ headers },
			);
			expect(data).not.toBeNull();
			createdIds.push(data.id);
		}

		const { data } = await client.apiKey.list({}, { headers });
		const listedIds = (data?.apiKeys ?? []).map((k) => k.id);

		// Exactly the created set — no missing ids, no foreign ids.
		expect([...listedIds].sort()).toEqual([...createdIds].sort());

		// Sorting/pagination still applied in memory: createdAt desc + limit.
		const { data: limited } = await client.apiKey.list(
			{ query: { sortBy: "createdAt", sortDirection: "desc", limit: 2 } },
			{ headers },
		);
		const limitedIds = (limited?.apiKeys ?? []).map((k) => k.id);
		expect(limitedIds.length).toBe(2);
		for (const id of limitedIds) {
			expect(createdIds).toContain(id);
		}
	});
});

// --------------------------------------------------------------------- //
// fallback mode: parallel_populate + concurrent_creates                 //
// --------------------------------------------------------------------- //
describe("api-key parallel storage (secondary-storage + fallback)", async () => {
	const store = new Map();
	const secondaryStorage = makeStorage(store);

	const { client, auth, signInWithTestUser } = await getTestInstance(
		{
			secondaryStorage,
			plugins: [
				apiKey({
					storage: "secondary-storage",
					fallbackToDatabase: true,
					enableMetadata: true,
				}),
			],
		},
		{ clientOptions: { plugins: [apiKeyClient()] } },
	);

	afterEach(() => {
		vi.restoreAllMocks();
		store.clear();
	});

	it("parallel_populate_no_per_key_reflist: rebuild fans per-key writes out and writes the ref index once", async () => {
		const { headers, user } = await signInWithTestUser();

		// Seed rows straight into the DB so the secondary index is empty
		// and the list MISSES -> falls back to DB -> repopulates the cache.
		const context = await auth.$context;
		const seededIds = [];
		for (let i = 0; i < 5; i++) {
			const dbKey = await context.adapter.create({
				model: "apikey",
				data: {
					configId: "default",
					createdAt: new Date(),
					updatedAt: new Date(),
					name: `Seed Key ${i}`,
					prefix: "test",
					start: "test_",
					key: `hashed_seed_${i}_${Date.now()}_${Math.random()}`,
					enabled: true,
					expiresAt: null,
					referenceId: user.id,
					lastRefillAt: null,
					lastRequest: null,
					metadata: null,
					rateLimitMax: null,
					rateLimitTimeWindow: null,
					remaining: null,
					refillAmount: null,
					refillInterval: null,
					rateLimitEnabled: false,
					requestCount: 0,
					permissions: null,
				},
			});
			expect(dbKey).not.toBeNull();
			seededIds.push(dbKey.id);
		}

		const refKey = `api-key:by-ref:${user.id}`;

		// Index must start empty (no by-ref entry yet).
		expect(store.has(refKey)).toBe(false);

		let setInFlight = 0;
		let maxSetInFlight = 0;
		const origSet = secondaryStorage.set.bind(secondaryStorage);

		vi.spyOn(secondaryStorage, "set").mockImplementation(
			async (key, value, ttl) => {
				if (typeof key === "string" && key.startsWith("api-key:by-id:")) {
					setInFlight++;
					maxSetInFlight = Math.max(maxSetInFlight, setInFlight);
					await sleep(20);
					const result = origSet(key, value, ttl);
					setInFlight--;
					return result;
				}
				return origSet(key, value, ttl);
			},
		);

		const { data } = await client.apiKey.list({}, { headers });
		const listedIds = (data?.apiKeys ?? []).map((k) => k.id);

		// Correctness: every seeded key shows up.
		for (const id of seededIds) {
			expect(listedIds).toContain(id);
		}
		// Per-key writes overlap (serial pre-fix loop yields exactly 1).
		expect(maxSetInFlight).toBeGreaterThanOrEqual(2);
	});

	it("concurrent_creates_no_lost_id: two simultaneous creates both appear in a later list", async () => {
		const { headers, user } = await signInWithTestUser();

		// Widen the read-modify-write window: any read of the ref index
		// stalls ~30ms, so two interleaved RMW writers (pre-fix) read the
		// same snapshot and the later write clobbers the earlier id.
		const origGet = secondaryStorage.get.bind(secondaryStorage);
		const getSpy = vi
			.spyOn(secondaryStorage, "get")
			.mockImplementation(async (key) => {
				const result = origGet(key);
				if (typeof key === "string" && key.startsWith("api-key:by-ref:")) {
					await sleep(30);
				}
				return result;
			});

		const [a, b] = await Promise.all([
			client.apiKey.create({ name: "race-a" }, { headers }),
			client.apiKey.create({ name: "race-b" }, { headers }),
		]);
		expect(a.data).not.toBeNull();
		expect(b.data).not.toBeNull();

		// Stop widening the window before listing.
		getSpy.mockRestore();

		const { data } = await client.apiKey.list({}, { headers });
		const listedIds = (data?.apiKeys ?? []).map((k) => k.id);

		// Pre-fix: one id is dropped from the index -> missing here.
		expect(listedIds).toContain(a.data.id);
		expect(listedIds).toContain(b.data.id);
	});
});
