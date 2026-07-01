// @ts-nocheck
//
// These tests exercise the highest-level pre-existing stable interface
// (`snapshotDataLogic.values.isWaitingForPlayableFullSnapshot`) so any of
// the alternative implementation strategies (`number | null` return-type
// fix vs. call-site sourceCount guard; storeUpdated dispatch vs. dropping
// selector memoisation) all pass.

import { expectLogic } from 'kea-test-utils'

import { SessionRecordingSnapshotSource } from '~/types'

import { setupSessionRecordingTest } from './__mocks__/test-setup'
import { snapshotDataLogic } from './snapshotDataLogic'

const SOURCE_A: SessionRecordingSnapshotSource = {
    source: 'blob_v2',
    start_timestamp: '2023-08-11T12:00:00.000000Z',
    end_timestamp: '2023-08-11T12:01:00.000000Z',
    blob_key: 'a',
}
const SOURCE_B: SessionRecordingSnapshotSource = {
    source: 'blob_v2',
    start_timestamp: '2023-08-11T12:01:00.000000Z',
    end_timestamp: '2023-08-11T12:02:00.000000Z',
    blob_key: 'b',
}

function tsMs(minute: number, second: number = 30): number {
    return new Date(`2023-08-11T12:0${minute}:${String(second).padStart(2, '0')}.000Z`).getTime()
}

describe('posthog-fix-replay-buffering verifier (#53893)', () => {
    let logic: ReturnType<typeof snapshotDataLogic.build> | undefined

    beforeEach(() => {
        // Default mock: two blob_v2 sources covering minute 0 and minute 1.
        // The same default is used by the in-repo store.test file. The actual
        // store contents are managed per-test below; this just stops the
        // /snapshots listing call from failing.
        setupSessionRecordingTest({ snapshotSources: [SOURCE_A, SOURCE_B] })
    })

    afterEach(() => {
        logic?.unmount()
        logic = undefined
    })

    it('enters seek mode when setTargetTimestamp called on empty store', async () => {
        // Defect A regression: at initial-load against a past-end ?t= URL,
        // setTargetTimestamp fires before the snapshot-source listing has
        // resolved, so the store is empty. The pre-fix code returned 0 from
        // getSourceIndexForTimestamp on an empty store, tripping the
        // `targetIndex === 0 && currentMode.kind === 'buffer_ahead'`
        // optimisation in setTargetTimestamp and skipping scheduler.seekTo.
        // Result: scheduler stayed in buffer_ahead, no seek-mode flip,
        // isWaitingForPlayableFullSnapshot stayed false.
        logic = snapshotDataLogic({
            sessionRecordingId: 'verify-defect-a',
            blobV2PollingDisabled: true,
        })
        logic.mount()

        // Pre-condition: store is mounted but has no sources yet.
        expect(logic.values.snapshotStore!.sourceCount).toBe(0)

        // Force-populate the selector memo at `false` so a Defect-A fix that
        // forgets to invalidate the selector along the seekTo path still
        // surfaces. (In an alternative design that drops memoisation
        // entirely, the pre-read also returns false but the second read
        // recomputes live and returns true — that alternative also passes.)
        expect(logic.values.isWaitingForPlayableFullSnapshot).toBe(false)

        logic.actions.setTargetTimestamp(tsMs(5, 0))
        await expectLogic(logic).toFinishAllListeners()

        // With ANY valid Defect-A fix that lets scheduler.seekTo run on the
        // empty-store path, paired with selector reactivity (or no
        // memoisation at all): the scheduler is now in seek mode, canPlayAt
        // is false (no data), so isWaitingForPlayableFullSnapshot is true.
        expect(logic.values.isWaitingForPlayableFullSnapshot).toBe(true)
    })

    it('isWaitingForPlayableFullSnapshot recovers after silent seek clear', async () => {
        // Defect B regression: LoadingScheduler.getSeekBatch can silently
        // clear seek mode (step 5: backward search exhausted with no
        // FullSnapshot found, no store mutation). The pre-fix selector
        // depended on storeVersion (which only bumps on store DATA changes),
        // so the pure-mode transition didn't invalidate the memo and
        // isWaitingForPlayableFullSnapshot stayed at the cached `true`,
        // poisoning the next checkBufferingCompleted read.
        logic = snapshotDataLogic({
            sessionRecordingId: 'verify-defect-b',
            blobV2PollingDisabled: true,
        })
        logic.mount()

        // Pre-load sources as loaded-but-empty: state='loaded' means the
        // seek-batch step-1 window-fill finds nothing to load, and empty
        // snapshot arrays mean no FullSnapshot exists anywhere. canPlayAt
        // is therefore always false, the step-3/4 backward search has
        // nothing to load, step-5 exhausts → silent clearSeek.
        const store = logic.values.snapshotStore!
        store.setSources([SOURCE_A, SOURCE_B])
        store.markLoaded(0, [])
        store.markLoaded(1, [])

        // Tell Kea about the sources. setSources preserves loaded entries
        // by blob_key, so the markLoaded above survives.
        logic.actions.loadSnapshotSourcesSuccess([SOURCE_A, SOURCE_B])
        await expectLogic(logic).toFinishAllListeners()

        // Drive the scheduler directly into seek mode. The same pattern is
        // used by the in-repo snapshotDataLogic.store.test file — cache.scheduler
        // is the pre-existing LoadingScheduler instance set up in
        // afterMount, not introduced by this task.
        const scheduler = (logic as any).cache.scheduler as {
            seekTo: (ts: number) => void
            currentMode: { kind: string }
        }
        scheduler.seekTo(tsMs(0, 30))

        // Pre-read: seek mode + no full snapshot → true. Populates the memo.
        expect(logic.values.isWaitingForPlayableFullSnapshot).toBe(true)

        // loadNextSnapshotSource calls scheduler.getNextBatch which traverses
        // step 1 (nothing unloaded) → step 2 (canPlayAt false) → step 3
        // (no FullSnapshot) → step 4 (nothing in backward search) → step 5
        // exhausted → clearSeek. Mode flips to buffer_ahead WITHOUT any
        // store mutation, isolating the selector-reactivity half of the fix
        // from anything that would incidentally bump storeVersion.
        logic.actions.loadNextSnapshotSource()
        await expectLogic(logic).toFinishAllListeners()

        // Sanity: the silent clear actually fired. If this assertion fails
        // the test setup is wrong, not the fix.
        expect(scheduler.currentMode.kind).toBe('buffer_ahead')

        // With ANY valid Defect-B fix (storeUpdated dispatch on the silent
        // transition + selector dep that bumps on dispatch, OR currentMode
        // moved into Kea state, OR removing selector memoisation): the
        // selector reflects the live scheduler mode and returns false.
        expect(logic.values.isWaitingForPlayableFullSnapshot).toBe(false)
    })

    it('does not enter seek for source 0 when populated store is in buffer_ahead', async () => {
        // Pass-to-pass regression guard for the existing source-0-buffer-
        // ahead optimisation. When the store IS populated and the target
        // timestamp resolves to source 0 while currentMode.kind ===
        // 'buffer_ahead', setTargetTimestamp should short-circuit instead
        // of calling scheduler.seekTo — buffer-ahead loading already
        // starts from index 0, so a redundant seek would just thrash the
        // scheduler.
        //
        // Catches naïve Defect-A fixes that always fall through to
        // scheduler.seekTo (e.g. by removing the optimisation entirely
        // or by checking only `targetIndex === null` without keeping the
        // `targetIndex === 0` short-circuit). Those would put the
        // scheduler into seek mode here and break normal forward
        // playback at the start of a recording.
        logic = snapshotDataLogic({
            sessionRecordingId: 'verify-p2p-source-0-optimisation',
            blobV2PollingDisabled: true,
        })
        logic.mount()

        // Populate the store with two sources so sourceCount > 0 and
        // tsMs(0, 0) resolves to source 0.
        logic.actions.loadSnapshotSourcesSuccess([SOURCE_A, SOURCE_B])
        await expectLogic(logic).toFinishAllListeners()
        logic.values.snapshotStore!.setSources([SOURCE_A, SOURCE_B])

        // Target is the very start of source A; mode is the default
        // buffer_ahead (no prior seek).
        logic.actions.setTargetTimestamp(tsMs(0, 0))
        await expectLogic(logic).toFinishAllListeners()

        // The optimisation should hold — no seek-mode flip — both pre-
        // fix and post-fix.
        expect(logic.values.isWaitingForPlayableFullSnapshot).toBe(false)
    })
})
