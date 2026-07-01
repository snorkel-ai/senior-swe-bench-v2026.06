// Copyright 2026 The Gitea Authors. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Pass-to-pass structural guard for the project-column-picker feature
// (`backend_no_regression`).
//
// The feature refactors models/project's internal column-move plumbing
// (the (*Column).moveIssuesToAnotherColumn method becomes a package
// function, and the inline "max(sorting)+1" query is extracted into a
// shared helper reused by three call sites). None of that internal
// surface is stable across valid implementations, so this test never
// touches it. Instead it drives the pre-existing, stable, exported
// public entry point DeleteColumnByID, whose contract is unchanged by
// the feature on BOTH trees:
//
//   - deleting a non-default column re-homes that column's issues onto
//     the project's default column, and
//   - the re-homed issues are appended at the END of the default
//     column's ordering (one past its current max sorting), so they do
//     not collide with issues already there.
//
// This behavior is green on the pre-fix tree and must remain green
// after the change — that is exactly the regression this guard pins.
//
// Fixtures used (models/fixtures/, do NOT add to them):
//   - project 1 "First project" (repo user2/repo1) has columns
//     1 "To Do" (default), 2 "In Progress", 3 "Done".
//   - project_issue: issue 1 -> column 1 (sorting 0), issue 3 ->
//     column 2, issue 5 -> column 3.

package project

import (
	"testing"

	"code.gitea.io/gitea/models/unittest"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// findByIssueID returns the ProjectIssue row for the given issue id from
// a column's issue list, or nil if it is not present.
func findByIssueID(issues []*ProjectIssue, issueID int64) *ProjectIssue {
	for _, pi := range issues {
		if pi.IssueID == issueID {
			return pi
		}
	}
	return nil
}

// TestVerifyColumnDeletionRehomesIssues deletes a populated non-default
// column and asserts its issue is re-homed onto the default column,
// appended at the end of the existing ordering (not colliding with the
// issue already in the default column).
func TestVerifyColumnDeletionRehomesIssues(t *testing.T) {
	require.NoError(t, unittest.PrepareTestDatabase())

	// Default column 1 ("To Do") starts with issue 1 at sorting 0.
	defaultColumn := unittest.AssertExistsAndLoadBean(t, &Column{ID: 1, ProjectID: 1})
	require.True(t, defaultColumn.Default, "column 1 is expected to be the default column")

	before, err := defaultColumn.GetIssues(t.Context())
	require.NoError(t, err)
	require.Len(t, before, 1)
	require.EqualValues(t, 1, before[0].IssueID)
	require.EqualValues(t, 0, before[0].Sorting)

	// Column 2 ("In Progress") holds issue 3.
	column2 := unittest.AssertExistsAndLoadBean(t, &Column{ID: 2, ProjectID: 1})
	mid, err := column2.GetIssues(t.Context())
	require.NoError(t, err)
	require.Len(t, mid, 1)
	require.EqualValues(t, 3, mid[0].IssueID)

	// Delete the non-default column through the stable public entry
	// point. This must re-home issue 3 onto the default column.
	require.NoError(t, DeleteColumnByID(t.Context(), column2.ID))

	// The deleted column no longer exists.
	_, err = GetColumn(t.Context(), column2.ID)
	require.Error(t, err)
	assert.True(t, IsErrProjectColumnNotExist(err), "deleted column should report not-exist")

	// The default column now holds both issues; the re-homed issue is
	// appended at the end (one past the prior max sorting of 0), so it
	// does not collide with issue 1.
	after, err := defaultColumn.GetIssues(t.Context())
	require.NoError(t, err)
	require.Len(t, after, 2)

	keptIssue := findByIssueID(after, 1)
	require.NotNil(t, keptIssue, "issue 1 should remain in the default column")
	assert.EqualValues(t, 0, keptIssue.Sorting, "the pre-existing issue keeps its sorting")

	movedIssue := findByIssueID(after, 3)
	require.NotNil(t, movedIssue, "issue 3 should be re-homed to the default column")
	assert.EqualValues(t, 1, movedIssue.ProjectColumnID, "re-homed issue lands in the default column")
	assert.Greater(t, movedIssue.Sorting, keptIssue.Sorting,
		"re-homed issue is appended after the existing issue, not colliding with it")
	assert.EqualValues(t, 1, movedIssue.Sorting, "re-homed issue is appended one past the prior max sorting")
}
