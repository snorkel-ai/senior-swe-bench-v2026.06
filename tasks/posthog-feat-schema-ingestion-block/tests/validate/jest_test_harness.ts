// @ts-nocheck
// Pure utilities for jest-nodejs validation stories: build a pipeline
// input payload and classify drop/ok results. Per-trial adapters for the
// step factory live in the test file, not here.

/** Build a (event, team) input record matching the pipeline step shape. */
export function buildPipelineInput(opts: {
    eventName: string
    properties?: Record<string, unknown> | undefined
    teamId?: number
    distinctId?: string
    eventUuid?: string
}): any {
    const teamId = opts.teamId ?? 1
    return {
        event: {
            event: opts.eventName,
            distinct_id: opts.distinctId ?? 'val-distinct-id',
            team_id: teamId,
            uuid: opts.eventUuid ?? '00000000-0000-0000-0000-000000000001',
            ip: '127.0.0.1',
            site_url: 'https://example.com',
            now: '2025-01-01T00:00:00Z',
            properties: opts.properties,
        },
        team: { id: teamId, name: `Test Team ${teamId}` },
    }
}

/** Pipeline result helpers that use lazy require to stay frontend-safe. */
export function isDropResult(result: any): boolean {
    const { PipelineResultType } = require('~/ingestion/pipelines/results')
    return result?.type === PipelineResultType.DROP
}

export function isOkResult(result: any): boolean {
    const { PipelineResultType } = require('~/ingestion/pipelines/results')
    return result?.type === PipelineResultType.OK
}
