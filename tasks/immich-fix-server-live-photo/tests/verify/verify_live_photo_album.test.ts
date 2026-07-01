// @ts-nocheck
//
// Behavioural verifier: a Live Photo's motion video must inherit the still
// photo's album, date, and EXIF tokens when migrated. Asserts on the path
// recorded by `mocks.asset.update` for the motion video, through the
// pre-existing StorageTemplateService migration interface.
//
// The migration runs in two situations that each move the motion via their own
// call site: the full library migration an admin runs (e.g. after changing the
// template) and the per-asset pass that fires when a single photo is imported.
// All three metadata dimensions (album / date / EXIF) flow through the same
// shared move within a situation, so the three dimensions are exercised via the
// library migration and one album case pins the freshly-imported-photo path.

import { defaults } from 'src/config';
import { AssetType } from 'src/enum';
import { StorageTemplateService } from 'src/services/storage-template.service';
import { AlbumFactory } from 'test/factories/album.factory';
import { AssetFactory } from 'test/factories/asset.factory';
import { userStub } from 'test/fixtures/user.stub';
import { getForStorageTemplate } from 'test/mappers';
import { makeStream, newTestService, ServiceMocks } from 'test/utils';

describe('live photo album / date migration', () => {
  let sut: StorageTemplateService;
  let mocks: ServiceMocks;

  beforeEach(() => {
    ({ sut, mocks } = newTestService(StorageTemplateService));
    mocks.systemMetadata.get.mockResolvedValue({ storageTemplate: { enabled: true } });
    sut.onConfigInit({ newConfig: defaults });
    // move.create stub mirroring the requested newPath, so the post-create
    // branch has the standard shape to work with.
    mocks.move.create.mockImplementation(async (input: any) => ({
      id: 'mv-' + input.entityId,
      entityId: input.entityId,
      pathType: input.pathType,
      oldPath: input.oldPath,
      newPath: input.newPath,
    }));
  });

  it("should migrate a live photo's motion video into the still photo's album folder", async () => {
    const motionAsset = AssetFactory.from({
      type: AssetType.Video,
      fileCreatedAt: new Date('2022-06-19T23:41:36.910Z'),
    })
      .exif()
      .build();
    const stillAsset = AssetFactory.from({
      livePhotoVideoId: motionAsset.id,
      fileCreatedAt: new Date('2022-06-19T23:41:36.910Z'),
    })
      .exif()
      .build();
    const album = AlbumFactory.from().asset().build();

    const config = structuredClone(defaults);
    config.storageTemplate.template =
      '{{y}}/{{#if album}}{{album}}{{else}}other{{/if}}/{{filename}}';
    sut.onConfigInit({ newConfig: config });

    // Bulk migration sees only the still in its stream; the verifier asserts
    // solely on the motion-video update produced via the still's livePhoto branch.
    mocks.assetJob.streamForStorageTemplateJob.mockReturnValue(
      makeStream([getForStorageTemplate(stillAsset)]),
    );
    mocks.user.getList.mockResolvedValue([userStub.user1]);
    mocks.assetJob.getForStorageTemplateJob.mockResolvedValueOnce(
      getForStorageTemplate(motionAsset),
    );

    // Only the still has an album row, and the mock is keyed on asset id (not
    // ownerId): looking up the still returns [album], the motion returns [].
    // This accepts any fix that threads the still's id, regardless of ownerId.
    mocks.album.getByAssetId.mockImplementation(async (_ownerId: string, assetId: string) => {
      if (assetId === stillAsset.id) return [album];
      return [];
    });

    await sut.handleMigration();

    const motionUpdate = (mocks.asset.update.mock.calls as any[]).find(
      (c) => c[0]?.id === motionAsset.id,
    );
    expect(motionUpdate).toBeDefined();
    // Motion video must land under the still photo's album folder, not {{else}}.
    expect(motionUpdate[0].originalPath).toContain(`/${album.albumName}/`);
    expect(motionUpdate[0].originalPath).not.toContain('/other/');
  });

  it("should migrate a live photo's motion video using the still photo's metadata for date tokens", async () => {
    // Motion and still have different fileCreatedAt so divergence is observable.
    const motionAsset = AssetFactory.from({
      type: AssetType.Video,
      fileCreatedAt: new Date('2020-01-15T10:00:00.000Z'),
    })
      .exif()
      .build();
    const stillAsset = AssetFactory.from({
      livePhotoVideoId: motionAsset.id,
      fileCreatedAt: new Date('2022-06-19T23:41:36.910Z'),
    })
      .exif()
      .build();

    const config = structuredClone(defaults);
    config.storageTemplate.template = '{{y}}/{{MM}}/{{filename}}';
    sut.onConfigInit({ newConfig: config });

    mocks.assetJob.streamForStorageTemplateJob.mockReturnValue(
      makeStream([getForStorageTemplate(stillAsset)]),
    );
    mocks.user.getList.mockResolvedValue([userStub.user1]);
    mocks.assetJob.getForStorageTemplateJob.mockResolvedValueOnce(
      getForStorageTemplate(motionAsset),
    );

    await sut.handleMigration();

    const motionUpdate = (mocks.asset.update.mock.calls as any[]).find(
      (c) => c[0]?.id === motionAsset.id,
    );
    expect(motionUpdate).toBeDefined();
    // Motion video must use the still's date tokens (2022/06), not its own (2020/01).
    expect(motionUpdate[0].originalPath).toContain('/2022/06/');
    expect(motionUpdate[0].originalPath).not.toContain('/2020/01/');
  });

  it("should migrate a live photo's motion video using the still photo's EXIF for make/model substitutions", async () => {
    // Motion and still have different EXIF make/model so divergence is observable.
    const motionAsset = AssetFactory.from({
      type: AssetType.Video,
      fileCreatedAt: new Date('2022-06-19T23:41:36.910Z'),
    })
      .exif({ make: 'GenericEncoder', model: 'h264' })
      .build();
    const stillAsset = AssetFactory.from({
      livePhotoVideoId: motionAsset.id,
      fileCreatedAt: new Date('2022-06-19T23:41:36.910Z'),
    })
      .exif({ make: 'Apple', model: 'iPhone14' })
      .build();

    const config = structuredClone(defaults);
    config.storageTemplate.template = '{{make}}/{{model}}/{{filename}}';
    sut.onConfigInit({ newConfig: config });

    mocks.assetJob.streamForStorageTemplateJob.mockReturnValue(
      makeStream([getForStorageTemplate(stillAsset)]),
    );
    mocks.user.getList.mockResolvedValue([userStub.user1]);
    mocks.assetJob.getForStorageTemplateJob.mockResolvedValueOnce(
      getForStorageTemplate(motionAsset),
    );

    await sut.handleMigration();

    const motionUpdate = (mocks.asset.update.mock.calls as any[]).find(
      (c) => c[0]?.id === motionAsset.id,
    );
    expect(motionUpdate).toBeDefined();
    // Motion video must use the still's EXIF (Apple/iPhone14), not its own.
    expect(motionUpdate[0].originalPath).toContain('/Apple/iPhone14/');
    expect(motionUpdate[0].originalPath).not.toContain('/GenericEncoder/');
  });

  it("should migrate a freshly imported live photo's motion video into the still photo's album folder", async () => {
    const motionAsset = AssetFactory.from({
      type: AssetType.Video,
      fileCreatedAt: new Date('2022-06-19T23:41:36.910Z'),
    })
      .exif()
      .build();
    const stillAsset = AssetFactory.from({
      livePhotoVideoId: motionAsset.id,
      fileCreatedAt: new Date('2022-06-19T23:41:36.910Z'),
    })
      .exif()
      .build();
    const album = AlbumFactory.from().asset().build();

    const config = structuredClone(defaults);
    config.storageTemplate.template =
      '{{y}}/{{#if album}}{{album}}{{else}}other{{/if}}/{{filename}}';
    sut.onConfigInit({ newConfig: config });

    // Importing one photo fetches the still by its id, then the motion by the
    // still's livePhotoVideoId. Key the mock on id so neither call order nor
    // the (post-fix) includeHidden option matters.
    mocks.user.get.mockResolvedValue(userStub.user1);
    mocks.assetJob.getForStorageTemplateJob.mockImplementation(async (id: string) => {
      if (id === stillAsset.id) return getForStorageTemplate(stillAsset);
      if (id === motionAsset.id) return getForStorageTemplate(motionAsset);
      return undefined;
    });

    // Only the still has an album row, keyed on asset id: the still resolves to
    // [album], the motion to []. Passes for any fix that threads the still's id.
    mocks.album.getByAssetId.mockImplementation(async (_ownerId: string, assetId: string) => {
      if (assetId === stillAsset.id) return [album];
      return [];
    });

    await sut.handleMigrationSingle({ id: stillAsset.id });

    const motionUpdate = (mocks.asset.update.mock.calls as any[]).find(
      (c) => c[0]?.id === motionAsset.id,
    );
    expect(motionUpdate).toBeDefined();
    // Motion video must land under the still photo's album folder, not {{else}}.
    expect(motionUpdate[0].originalPath).toContain(`/${album.albumName}/`);
    expect(motionUpdate[0].originalPath).not.toContain('/other/');
  });
});
