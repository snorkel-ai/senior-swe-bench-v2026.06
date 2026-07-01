// @ts-nocheck
//
// Tests through the pre-existing, signature-stable VersionService surface
// (getVersion, handleVersionCheck, onWebsocketConnection) and the static
// ServerVersionResponseDto.fromSemVer — the interfaces the repo's own
// version.service.spec.ts exercises. Observation points are the mocked
// websocket repository (`mocks.websocket.clientBroadcast` / `clientSend`),
// matching that spec.
//
// Independent of internal decomposition: does NOT reference any symbol the
// implementation introduces, and does NOT pin the NAME of the version object's
// pre-release field — it locates that field by value (the single number-or-null
// field beyond major/minor/patch), so `prerelease`, `preRelease`, `rc`, etc. all
// pass. Channel-aware behaviour is exercised ONLY through the DEFAULT (stable)
// config path, so any valid implementation produces the same observable
// behaviour and passes.
//
// serverVersion is mocked to 3.0.0 (as the version.service.spec.ts does) so the
// stable/pre-release boundary is deterministic and TZ-independent.

import { DateTime } from 'luxon';
import { SemVer } from 'semver';
import { defaults } from 'src/config';
import { ServerVersionResponseDto } from 'src/dtos/server.dto';
import { JobName, JobStatus, SystemMetadataKey } from 'src/enum';
import { VersionService } from 'src/services/version.service';
import { newTestService, ServiceMocks } from 'test/utils';

const mockVersionResponse = (version: string) => ({
  version,
  published_at: DateTime.utc().toISO(),
});

// The reported version object carries major/minor/patch plus the release's
// numeric pre-release index (a number for a pre-release, null for a stable
// release). We locate that index field by its VALUE — the single own field
// beyond major/minor/patch whose value is a number or null — never by name, so
// any valid implementation is free to call it `prerelease`, `preRelease`, `rc`,
// etc. The numeric-extraction behaviour is what this asserts, not the spelling.
const prereleaseValueOf = (versionObj: Record<string, unknown>) => {
  const extra = Object.entries(versionObj).filter(([key]) => !['major', 'minor', 'patch'].includes(key));
  const numericOrNull = extra.filter(([, value]) => value === null || typeof value === 'number');
  expect(numericOrNull).toHaveLength(1);
  return numericOrNull[0][1];
};

describe('VersionService release-candidate support', () => {
  let sut: VersionService;
  let mocks: ServiceMocks;

  beforeEach(() => {
    ({ sut, mocks } = newTestService(VersionService));
    mocks.cron.create.mockResolvedValue();
    mocks.cron.update.mockResolvedValue();
  });

  beforeAll(() => {
    vitest.mock(import('src/constants.js'), async () => ({
      ...(await vitest.importActual<typeof import('src/constants.js')>('src/constants.js')),
      serverVersion: new SemVer('v3.0.0'),
    }));
  });

  afterAll(() => {
    vitest.unmock(import('src/constants.js'));
  });

  // ---- V1: getVersion exposes a nullable prerelease number ---------------
  describe('getVersion', () => {
    it('includes a prerelease field that is null for a stable server', () => {
      const version = sut.getVersion();
      expect(version).toMatchObject({ major: 3, minor: 0, patch: 0 });
      expect(prereleaseValueOf(version)).toBeNull();
    });
  });

  // ---- V2: fromSemVer extracts the NUMERIC prerelease index --------------
  describe('ServerVersionResponseDto.fromSemVer', () => {
    it('extracts the numeric prerelease index from a release candidate', () => {
      const version = ServerVersionResponseDto.fromSemVer(new SemVer('3.0.1-rc.5'));
      expect(version).toMatchObject({ major: 3, minor: 0, patch: 1 });
      // The pre-release index is the NUMBER 5, not the raw `rc` tag string.
      expect(prereleaseValueOf(version)).toBe(5);
    });

    it('reports a null prerelease for a plain stable version', () => {
      const version = ServerVersionResponseDto.fromSemVer(new SemVer('3.0.0'));
      expect(version).toMatchObject({ major: 3, minor: 0, patch: 0 });
      expect(prereleaseValueOf(version)).toBeNull();
    });
  });

  // ---- V3: channel-aware availability on the DEFAULT (stable) channel ----
  describe('handleVersionCheck (default / stable channel)', () => {
    it('reports a newer STABLE release as available', async () => {
      mocks.serverInfo.getLatestRelease.mockResolvedValue(mockVersionResponse('3.0.1'));
      await expect(sut.handleVersionCheck()).resolves.toEqual(JobStatus.Success);
      expect(mocks.websocket.clientBroadcast).toHaveBeenCalledWith('on_new_release', expect.any(Object));
    });

    it('does NOT report a newer PRE-RELEASE as available (the gt-vs-rc trap)', async () => {
      mocks.serverInfo.getLatestRelease.mockResolvedValue(mockVersionResponse('3.0.1-rc.0'));
      await expect(sut.handleVersionCheck()).resolves.toEqual(JobStatus.Success);
      expect(mocks.websocket.clientBroadcast).not.toHaveBeenCalled();
    });
  });

  // ---- V4: onWebsocketConnection sends a structured version object -------
  describe('onWebsocketConnection', () => {
    it('sends the running server version as a structured object, not a raw SemVer', async () => {
      await sut.onWebsocketConnection({ userId: '42' });
      const call = mocks.websocket.clientSend.mock.calls.find((c) => c[0] === 'on_server_version' && c[1] === '42');
      expect(call).toBeDefined();
      const version = call[2];
      // A structured object (major/minor/patch + the nullable pre-release index),
      // not a raw SemVer instance or version string.
      expect(version).toMatchObject({ major: 3, minor: 0, patch: 0 });
      expect(prereleaseValueOf(version)).toBeNull();
    });
  });

  // ---- V5: channel-independent regressions (pass on pre-fix AND post-fix)-
  describe('regression: pre-existing version-check behaviour is preserved', () => {
    it('skips the check when the version check is disabled', async () => {
      mocks.systemMetadata.get.mockResolvedValue({ newVersionCheck: { enabled: false } });
      await expect(sut.handleVersionCheck()).resolves.toEqual(JobStatus.Skipped);
      expect(mocks.serverInfo.getLatestRelease).not.toHaveBeenCalled();
      expect(mocks.websocket.clientBroadcast).not.toHaveBeenCalled();
    });

    it('does not notify when the latest release equals the running version', async () => {
      mocks.serverInfo.getLatestRelease.mockResolvedValue(mockVersionResponse('3.0.0'));
      await expect(sut.handleVersionCheck()).resolves.toEqual(JobStatus.Success);
      // The cached release IS recorded as the running version, and nothing is
      // broadcast. Use objectContaining so an implementation that persists extra
      // internal state alongside it (e.g. the resolved channel) is not rejected.
      expect(mocks.systemMetadata.set).toHaveBeenCalledWith(
        SystemMetadataKey.VersionCheckState,
        expect.objectContaining({ checkedAt: expect.any(String), releaseVersion: '3.0.0' }),
      );
      expect(mocks.websocket.clientBroadcast).not.toHaveBeenCalled();
    });

    it('returns Failed and warns when the upstream release lookup throws', async () => {
      mocks.serverInfo.getLatestRelease.mockRejectedValue(new Error('Version service is down'));
      await expect(sut.handleVersionCheck()).resolves.toEqual(JobStatus.Failed);
      expect(mocks.systemMetadata.set).not.toHaveBeenCalled();
      expect(mocks.websocket.clientBroadcast).not.toHaveBeenCalled();
      expect(mocks.logger.warn).toHaveBeenCalled();
    });

    it('queues a version check job when the version check transitions to enabled', async () => {
      await sut.onConfigUpdate({
        oldConfig: { ...defaults, newVersionCheck: { ...defaults.newVersionCheck, enabled: false } },
        newConfig: { ...defaults, newVersionCheck: { ...defaults.newVersionCheck, enabled: true } },
      });
      expect(mocks.job.queue).toHaveBeenCalledWith({ name: JobName.VersionCheck, data: {} });
    });
  });
});
