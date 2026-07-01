// @ts-nocheck
//
// The recently-added feature adds an implementation-designed selector (a query
// option + value, or a flag) plus a per-asset upload-timestamp response field.
// Those shapes are not fixed by the task, so the new behaviour cannot be pinned
// in a static verifier without coupling to one implementation; it is covered by
// the validation stories, which discover the shape from the implementing source.
//
// This verifier guards only the pre-existing default behaviour the rollout must
// not change: with no ordering option, the timeline still groups/selects by
// CAPTURE date. It runs at immich's medium tier (real Postgres) through the
// stable `TimelineService.getTimeBuckets` / `getTimeBucket` surface and
// references no new parameter, value, or field, so it passes on any valid
// implementation.

import { Kysely } from 'kysely';
import { AccessRepository } from 'src/repositories/access.repository';
import { AssetRepository } from 'src/repositories/asset.repository';
import { LoggingRepository } from 'src/repositories/logging.repository';
import { PartnerRepository } from 'src/repositories/partner.repository';
import { DB } from 'src/schema';
import { TimelineService } from 'src/services/timeline.service';
import { newMediumService } from 'test/medium.factory';
import { factory } from 'test/small.factory';
import { getKyselyDB } from 'test/utils';

let defaultDatabase: Kysely<DB>;

const setup = (db?: Kysely<DB>) => {
  return newMediumService(TimelineService, {
    database: db || defaultDatabase,
    real: [AssetRepository, AccessRepository, PartnerRepository],
    mock: [LoggingRepository],
  });
};

beforeAll(async () => {
  defaultDatabase = await getKyselyDB();
});

describe('timeline default ordering preserved (pass-to-pass)', () => {
  // default_buckets_by_capture_month: with no ordering option the month buckets
  // are grouped by CAPTURE date — the pre-existing behaviour. Two assets both
  // captured Jan 1970 (but uploaded in different 2020 months) collapse to a
  // single capture-month bucket of count 2.
  it('getTimeBuckets groups by capture month with no ordering option', async () => {
    const { sut, ctx } = setup();
    const { user } = await ctx.newUser();
    const auth = factory.auth({ user });

    await ctx.newAsset({
      ownerId: user.id,
      localDateTime: new Date('1970-01-15T00:00:00.000Z'),
      createdAt: new Date('2020-02-10T00:00:00.000Z'),
    });
    await ctx.newAsset({
      ownerId: user.id,
      localDateTime: new Date('1970-01-20T00:00:00.000Z'),
      createdAt: new Date('2020-03-10T00:00:00.000Z'),
    });

    const buckets = await sut.getTimeBuckets(auth, {});
    // Compare only the pre-existing fields so an implementation that adds
    // extra per-bucket fields still passes this pass-to-pass guard.
    expect(buckets.map(({ count, timeBucket }) => ({ count, timeBucket }))).toEqual([
      { count: 2, timeBucket: '1970-01-01' },
    ]);
  });

  // default_bucket_membership_by_capture: with no ordering option a single
  // bucket is selected by CAPTURE month. Both assets captured Jan 1970 are
  // returned for the Jan-1970 bucket (membership only — no assumption about the
  // pre-existing intra-bucket order).
  it('getTimeBucket selects by capture month with no ordering option', async () => {
    const { sut, ctx } = setup();
    const { user } = await ctx.newUser();
    const auth = factory.auth({ user });

    const { asset: a1 } = await ctx.newAsset({
      ownerId: user.id,
      localDateTime: new Date('1970-01-10T00:00:00.000Z'),
      createdAt: new Date('2020-02-10T00:00:00.000Z'),
    });
    await ctx.newExif({ assetId: a1.id, make: 'Canon' });
    const { asset: a2 } = await ctx.newAsset({
      ownerId: user.id,
      localDateTime: new Date('1970-01-25T00:00:00.000Z'),
      createdAt: new Date('2020-03-10T00:00:00.000Z'),
    });
    await ctx.newExif({ assetId: a2.id, make: 'Canon' });

    const response = JSON.parse(await sut.getTimeBucket(auth, { timeBucket: '1970-01-01' }));
    expect(response.id.length).toBe(2);
    expect(response.id).toContain(a1.id);
    expect(response.id).toContain(a2.id);
  });
});
