// @ts-nocheck
// Test harness for jest-frontend validation stories. Uses lazy require()
// inside function bodies so the test file's jest.mock() calls register
// before any module loads.

/**
 * Render <EventDefinitionSchema> for a definition with a schema attached,
 * with kea-loader network calls mocked. Returns the rendered DOM root.
 * Pass `enforcement_mode` on `definition` to pre-populate it; the canned
 * GET returns the same object so loaders see a consistent value.
 */
export interface RenderResult {
    container: HTMLElement
    body: HTMLElement
}

export async function renderEventDefinitionSchemaPanel(definition: any): Promise<RenderResult> {
    const { initKeaTests } = require('~/test/init')
    const { useMocks } = require('~/mocks/jest')
    const { render, waitFor } = require('@testing-library/react')
    const React = require('react')

    initKeaTests()

    // Mount teamLogic so loaders gated on currentTeamId fire.
    const { teamLogic } = require('scenes/teamLogic')
    teamLogic.mount()
    teamLogic.actions.loadCurrentTeamSuccess({
        id: 1,
        name: 'Test Team',
        uuid: 'test-team-uuid',
        api_token: 'test-api-token',
    } as any)

    // Mock the GETs the component's loaders need.
    useMocks({
        get: {
            '/api/projects/:teamId/event_definitions/:id': definition,
            '/api/projects/:teamId/event_schemas': {
                results: [
                    {
                        id: 'schema-fixture-1',
                        event_definition: definition.id,
                        property_group: {
                            id: 'group-fixture-1',
                            name: 'Schema Fixture Group',
                            properties: [
                                {
                                    id: 'prop-fixture-1',
                                    name: 'fixture_prop',
                                    property_type: 'String',
                                    is_required: true,
                                    description: '',
                                    order: 0,
                                },
                            ],
                        },
                    },
                ],
            },
            '/api/projects/:teamId/schema_property_groups/': {
                results: [
                    {
                        id: 'group-fixture-1',
                        name: 'Schema Fixture Group',
                        properties: [
                            {
                                id: 'prop-fixture-1',
                                name: 'fixture_prop',
                                property_type: 'String',
                                is_required: true,
                                description: '',
                                order: 0,
                            },
                        ],
                    },
                ],
            },
        },
        patch: {
            '/api/projects/:teamId/event_definitions/:id': () => [200, definition],
            '/api/projects/:teamId/schema_property_groups/:id/': () => [200, {}],
        },
        post: {
            '/api/projects/:teamId/event_schemas/': () => [201, {}],
            '/api/projects/:teamId/schema_property_groups/': () => [201, {}],
        },
    })

    // Require AFTER mocks register so kea-loaders pick up the mock URLs.
    const { EventDefinitionSchema } = require(
        'scenes/data-management/events/EventDefinitionSchema'
    )

    const { container } = render(React.createElement(EventDefinitionSchema, { definition }))

    // Wait for kea-loaders to populate eventSchemas so the toggle is
    // interactive rather than disabled with a "no schemas" reason.
    await waitFor(
        () => {
            const hasGroup = container.textContent?.includes('Schema Fixture Group')
            const hasControl =
                container.querySelector('input[type="checkbox"]') ||
                container.querySelector('[role="switch"]')
            if (!hasGroup && !hasControl) {
                throw new Error('component still loading')
            }
        },
        { timeout: 5000 }
    )

    return {
        container,
        body: document.body as HTMLElement,
    }
}

