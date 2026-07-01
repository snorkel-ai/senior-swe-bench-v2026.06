// @ts-nocheck
// This harness uses lazy imports (require inside function body) so that
// the test file's jest.mock calls are hoisted and registered BEFORE
// these modules are loaded. Do NOT convert to top-level imports.

export function discoverNewActionKey(): string | undefined {
    const { APPROVAL_ACTIONS } = require('scenes/approvals/utils')
    const knownKeys = new Set(['feature_flag.enable', 'feature_flag.disable'])
    return Object.keys(APPROVAL_ACTIONS).find((k: string) => !knownKeys.has(k))
}

export async function renderPolicyEditModal(
    conditionsObj: Record<string, unknown>
): Promise<HTMLElement> {
    const { initKeaTests } = require('~/test/init')
    const { useMocks } = require('~/mocks/jest')
    const { MOCK_TEAM_ID } = require('lib/api.mock')
    const { render, screen, waitFor, fireEvent, act, cleanup } = require('@testing-library/react')
    const { APPROVAL_ACTIONS } = require('scenes/approvals/utils')
    const { ApprovalPolicies } = require('scenes/settings/organization/Approvals/ApprovalPolicies')

    const newKey = discoverNewActionKey()
    if (!newKey) throw new Error('No new action key found in APPROVAL_ACTIONS')

    initKeaTests()

    useMocks({
        get: {
            [`/api/environments/${MOCK_TEAM_ID}/approval_policies/`]: {
                results: [
                    {
                        id: 'test-policy',
                        action_key: newKey,
                        enabled: true,
                        conditions: conditionsObj,
                        approver_config: { users: [1], roles: [], quorum: 1 },
                        allow_self_approve: true,
                        bypass_roles: [],
                    },
                ],
            },
            '/api/organizations/@current/members/': { results: [{ user: { id: 1 } }] },
            '/api/organizations/@current/roles/': { results: [] },
        },
    })

    render(<ApprovalPolicies />)

    const actionLabel = APPROVAL_ACTIONS[newKey].label
    await waitFor(() => {
        expect(screen.getByText(actionLabel)).toBeTruthy()
    })

    // Use fireEvent (not userEvent) for overlay interactions — userEvent's
    // full event chain triggers PostHog's outside-click handler and closes
    // the Popover before the action completes.
    const moreBtn = screen.getByLabelText('more')
    fireEvent.click(moreBtn)

    // Wrap in act() to flush all React state updates + pending timers
    // before proceeding. Raw setTimeout callbacks without act() can fire
    // after jsdom teardown and crash with null _document.
    await act(async () => {
        await new Promise((r) => setTimeout(r, 50))
    })
    const editEls = screen.queryAllByText('Edit')
    if (editEls.length === 0) throw new Error('Edit button not found after More click')
    fireEvent.click(editEls[0])

    // Flush modal mount
    await act(async () => {
        await new Promise((r) => setTimeout(r, 50))
    })

    // Verify modal opened
    await waitFor(
        () => {
            expect(screen.getByText(/edit approval policy/i)).toBeTruthy()
        },
        { timeout: 5000 }
    )

    // Flush any residual React state before returning.
    await act(async () => {})

    // Capture the modal element before unmounting.
    const modalElement = (screen.getByText(/edit approval policy/i).closest('[role="dialog"]') ||
        document.body) as HTMLElement

    // Explicitly unmount all components NOW — before the test function returns.
    // react-modal's ariaAppHider registers DOM listeners that fire as pending
    // setImmediate/microtask callbacks. Without explicit cleanup here, jest tears
    // down jsdom (sets window._document=null) before those callbacks complete,
    // causing "Cannot read properties of null (reading '_location')".
    await act(async () => {
        cleanup()
    })

    // After act(cleanup), drain any remaining setImmediate callbacks that
    // landed outside React's scheduler (e.g. jsdom/react-modal DOM housekeeping).
    // Use setTimeout(0) as a cross-environment fallback — setImmediate is not
    // available in all jest/jsdom configurations.
    await new Promise<void>((r) => setTimeout(r, 0))

    return modalElement
}
