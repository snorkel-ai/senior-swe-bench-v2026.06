// Copyright 2026 The Gitea Authors. All rights reserved.
// SPDX-License-Identifier: MIT

// Internal-package tests for CreatePushPullComment — the entry point every
// git push hook calls to record a CommentTypePullRequestPush row. All tests
// run against the bundled fixture PR id=2 (head=branch2, base=master on
// user2/repo1) and assert on database state rather than the return tuple, so
// they hold regardless of the function's return signature.

package pull

import (
	"context"
	"reflect"
	"strings"
	"testing"

	"code.gitea.io/gitea/models/db"
	issues_model "code.gitea.io/gitea/models/issues"
	"code.gitea.io/gitea/models/unittest"
	user_model "code.gitea.io/gitea/models/user"
	"code.gitea.io/gitea/modules/git"
	"code.gitea.io/gitea/modules/gitrepo"
	"code.gitea.io/gitea/modules/json"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// verifyZeroSHA is the canonical empty/null commit ID for sha1 repositories. A
// literal avoids depending on any helper a solution might add.
const verifyZeroSHA = "0000000000000000000000000000000000000000"

// verifyCallCreatePushPullComment invokes CreatePushPullComment via reflection so
// the verifier compiles whether a solution kept the 2-return signature or
// extended it. Returns the leading *Comment and the trailing error.
func verifyCallCreatePushPullComment(
	ctx context.Context,
	pusher *user_model.User,
	pr *issues_model.PullRequest,
	oldRef, newRef string,
	isForcePush bool,
) (*issues_model.Comment, error) {
	fn := reflect.ValueOf(CreatePushPullComment)
	out := fn.Call([]reflect.Value{
		reflect.ValueOf(ctx),
		reflect.ValueOf(pusher),
		reflect.ValueOf(pr),
		reflect.ValueOf(oldRef),
		reflect.ValueOf(newRef),
		reflect.ValueOf(isForcePush),
	})
	var comment *issues_model.Comment
	if v := out[0]; !v.IsNil() {
		comment = v.Interface().(*issues_model.Comment)
	}
	var err error
	if last := out[len(out)-1]; !last.IsNil() {
		if e, ok := last.Interface().(error); ok {
			err = e
		}
	}
	return comment, err
}

// verifyFindPushComments returns every CommentTypePullRequestPush row recorded
// against the given PR's issue.
func verifyFindPushComments(t *testing.T, pr *issues_model.PullRequest) []*issues_model.Comment {
	t.Helper()
	comments, err := issues_model.FindComments(t.Context(), &issues_model.FindCommentsOptions{
		IssueID: pr.IssueID,
		Type:    issues_model.CommentTypePullRequestPush,
	})
	require.NoError(t, err, "FindComments must not error")
	return comments
}

// verifyParsePushActionContent unmarshals a push comment's Content JSON into a
// PushActionContent struct — the persisted on-disk format the renderer reads.
func verifyParsePushActionContent(t *testing.T, c *issues_model.Comment) issues_model.PushActionContent {
	t.Helper()
	var data issues_model.PushActionContent
	require.NoError(t, json.Unmarshal([]byte(c.Content), &data),
		"comment %d: Content is not valid PushActionContent JSON: %q", c.ID, c.Content)
	return data
}

// verifyLoadFixturePR resets the test database, loads fixture PR id=2, and opens
// its base repo. Returns the pusher, PR, and a cleanup-aware git repo handle.
func verifyLoadFixturePR(t *testing.T) (
	*user_model.User,
	*issues_model.PullRequest,
	*git.Repository,
) {
	t.Helper()
	require.NoError(t, unittest.PrepareTestDatabase())
	pusher := unittest.AssertExistsAndLoadBean(t, &user_model.User{ID: 1})
	pr := unittest.AssertExistsAndLoadBean(t, &issues_model.PullRequest{ID: 2})
	require.NoError(t, pr.LoadIssue(t.Context()))
	require.NoError(t, pr.LoadBaseRepo(t.Context()))

	gitRepo, err := gitrepo.OpenRepository(t.Context(), pr.BaseRepo)
	require.NoError(t, err)
	t.Cleanup(func() { gitRepo.Close() })
	return pusher, pr, gitRepo
}

// TestVerifyPriorForcePushMarkerSurvivesNewForcePush covers the
// `prior_force_push_marker_survives_new_force_push` criterion (fail_to_pass):
// when a PR already has a force-push marker and a fresh force-push happens,
// the prior marker MUST stay in the database so every historical force-push
// event remains visible on the timeline.
func TestVerifyPriorForcePushMarkerSurvivesNewForcePush(t *testing.T) {
	pusher, pr, gitRepo := verifyLoadFixturePR(t)

	baseCommit, err := gitRepo.GetBranchCommit(pr.BaseBranch)
	require.NoError(t, err)
	headCommit, err := gitRepo.GetBranchCommit(pr.HeadBranch)
	require.NoError(t, err)

	require.NoError(t, db.TruncateBeans(t.Context(), &issues_model.Comment{}))

	// Probe marker with a recognisable commit_ids pair. The IDs need not be
	// reachable: the renderer treats marker payloads as opaque metadata.
	probeOldID := strings.Repeat("a", 40)
	probeNewID := strings.Repeat("b", 40)
	probe := issues_model.PushActionContent{
		IsForcePush: true,
		CommitIDs:   []string{probeOldID, probeNewID},
	}
	probeJSON, err := json.Marshal(probe)
	require.NoError(t, err)
	_, err = issues_model.CreateComment(t.Context(), &issues_model.CreateCommentOptions{
		Type:    issues_model.CommentTypePullRequestPush,
		Doer:    pusher,
		Repo:    pr.BaseRepo,
		Issue:   pr.Issue,
		Content: string(probeJSON),
	})
	require.NoError(t, err)

	// Trigger a fresh force-push (base → head).
	_, err = verifyCallCreatePushPullComment(
		t.Context(), pusher, pr,
		baseCommit.ID.String(), headCommit.ID.String(), true,
	)
	require.NoError(t, err, "force-push must not error on a normal base→head transition")

	// The prior marker's payload must still be observable after the
	// force-push, whether the row is kept in place or deleted and recreated
	// with equivalent content.
	comments := verifyFindPushComments(t, pr)
	probeContentSeen := false
	forcePushMarkers := 0
	for _, c := range comments {
		data := verifyParsePushActionContent(t, c)
		if data.IsForcePush {
			forcePushMarkers++
			if len(data.CommitIDs) == 2 && data.CommitIDs[0] == probeOldID && data.CommitIDs[1] == probeNewID {
				probeContentSeen = true
			}
		}
	}
	assert.True(t, probeContentSeen,
		"prior force-push marker payload [%s, %s] MUST still be present in the timeline after a new force-push (pre-fix wipes every push comment unconditionally — including old force-push markers); current force-push marker count=%d, total push comments=%d",
		probeOldID, probeNewID, forcePushMarkers, len(comments))

	// Expect at least 2 force-push markers: the prior probe plus the one
	// this call creates.
	assert.GreaterOrEqual(t, forcePushMarkers, 2,
		"after a new force-push on a PR that already had 1 force-push marker, the timeline must show at least 2 force-push markers (got %d, total push comments=%d)",
		forcePushMarkers, len(comments))
}

// TestVerifyPriorNonForcePushCommentWithKeptCommitsSurvives covers the
// `prior_non_force_push_comment_with_valid_commits_survives` criterion
// (fail_to_pass): when a PR has prior non-force-push comments whose recorded
// commit IDs are still reachable from the post-force-push branch tip, those
// comments MUST survive; only entries whose commits are gone should be
// removed.
//
// Uses headCommit.Parent(0) as the "kept commit P" — a real commit on the
// fixture branch, reachable from headCommit.
func TestVerifyPriorNonForcePushCommentWithKeptCommitsSurvives(t *testing.T) {
	pusher, pr, gitRepo := verifyLoadFixturePR(t)

	headCommit, err := gitRepo.GetBranchCommit(pr.HeadBranch)
	require.NoError(t, err)
	require.Greater(t, headCommit.ParentCount(), 0,
		"this scenario requires the fixture head branch to have at least one parent commit; got 0 parents")
	parentCommit, err := headCommit.Parent(0)
	require.NoError(t, err, "must be able to load headCommit's parent")
	keptCommitID := parentCommit.ID.String()

	require.NoError(t, db.TruncateBeans(t.Context(), &issues_model.Comment{}))

	// Probe non-force-push comment whose sole commit ID is parent P, which
	// is reachable from headCommit and so must survive the force-push.
	probe := issues_model.PushActionContent{
		IsForcePush: false,
		CommitIDs:   []string{keptCommitID},
	}
	probeJSON, err := json.Marshal(probe)
	require.NoError(t, err)
	_, err = issues_model.CreateComment(t.Context(), &issues_model.CreateCommentOptions{
		Type:    issues_model.CommentTypePullRequestPush,
		Doer:    pusher,
		Repo:    pr.BaseRepo,
		Issue:   pr.Issue,
		Content: string(probeJSON),
	})
	require.NoError(t, err)

	// Force-push with oldRef == headCommit, simulating a rebase/amend that
	// left the tip unchanged but rewrote earlier history.
	_, err = verifyCallCreatePushPullComment(
		t.Context(), pusher, pr,
		headCommit.ID.String(), headCommit.ID.String(), true,
	)
	require.NoError(t, err)

	// The kept commit ID must still appear in some non-force-push comment,
	// whether the prior row is kept (possibly filtered) or replaced by an
	// equivalent row whose commit_ids include P.
	comments := verifyFindPushComments(t, pr)
	keptCommitVisible := false
	nonForceComments := 0
	for _, c := range comments {
		data := verifyParsePushActionContent(t, c)
		if data.IsForcePush {
			continue
		}
		nonForceComments++
		for _, id := range data.CommitIDs {
			if id == keptCommitID {
				keptCommitVisible = true
				break
			}
		}
	}
	assert.True(t, keptCommitVisible,
		"after a force-push, commit %s (a parent of the new branch tip — still on the branch) MUST appear in some non-force-push push comment so the timeline still shows the corresponding push event; pre-fix wipes every push comment AND computes the new commit list from oldRef..newRef which excludes parent commits, so P disappears entirely; current non-force-push comment count=%d, total push comments=%d",
		keptCommitID, nonForceComments, len(comments))
}

// TestVerifyForcePushTolerantOfEmptyOldCommit covers the
// `force_push_tolerates_unreachable_old_commit` criterion (fail_to_pass):
// force-push must succeed and still record a force-push marker even when the
// old commit ID is the empty/zero SHA (the prior tip can be unreachable).
func TestVerifyForcePushTolerantOfEmptyOldCommit(t *testing.T) {
	pusher, pr, gitRepo := verifyLoadFixturePR(t)

	headCommit, err := gitRepo.GetBranchCommit(pr.HeadBranch)
	require.NoError(t, err)

	require.NoError(t, db.TruncateBeans(t.Context(), &issues_model.Comment{}))

	_, err = verifyCallCreatePushPullComment(
		t.Context(), pusher, pr,
		verifyZeroSHA, headCommit.ID.String(), true,
	)
	require.NoError(t, err,
		"force-push must succeed when the old commit ID is the empty/zero SHA — the prior tip can legitimately be unreachable on real force-pushes")

	// Find the force-push marker that this call recorded.
	comments := verifyFindPushComments(t, pr)
	var marker *issues_model.Comment
	for _, c := range comments {
		if verifyParsePushActionContent(t, c).IsForcePush {
			marker = c
			break
		}
	}
	require.NotNil(t, marker,
		"a force-push event must record exactly one force-push marker comment; none found after force-push from verifyZeroSHA → headCommit (push comments in DB: %d)",
		len(comments))

	data := verifyParsePushActionContent(t, marker)
	assert.Equal(t, []string{verifyZeroSHA, headCommit.ID.String()}, data.CommitIDs,
		"force-push marker for an empty old commit must record [verifyZeroSHA, headCommit] in commit_ids; got %v",
		data.CommitIDs)
}

// TestVerifyForcePushMarkerRecordsOldNewPair covers the
// `force_push_marker_records_old_and_new_head_pair` criterion (pass_to_pass):
// a normal force-push must produce a marker whose `commit_ids` is exactly
// `[oldHead, newHead]`, guarding against a fix that rewrites or drops the
// marker payload while reworking the deletion strategy.
func TestVerifyForcePushMarkerRecordsOldNewPair(t *testing.T) {
	pusher, pr, gitRepo := verifyLoadFixturePR(t)

	baseCommit, err := gitRepo.GetBranchCommit(pr.BaseBranch)
	require.NoError(t, err)
	headCommit, err := gitRepo.GetBranchCommit(pr.HeadBranch)
	require.NoError(t, err)

	require.NoError(t, db.TruncateBeans(t.Context(), &issues_model.Comment{}))

	_, err = verifyCallCreatePushPullComment(
		t.Context(), pusher, pr,
		baseCommit.ID.String(), headCommit.ID.String(), true,
	)
	require.NoError(t, err)

	comments := verifyFindPushComments(t, pr)
	var marker *issues_model.Comment
	for _, c := range comments {
		if verifyParsePushActionContent(t, c).IsForcePush {
			marker = c
			break
		}
	}
	require.NotNil(t, marker,
		"force-push must produce a force-push marker comment; none found (push comments in DB: %d)", len(comments))

	data := verifyParsePushActionContent(t, marker)
	assert.Equal(t, []string{baseCommit.ID.String(), headCommit.ID.String()}, data.CommitIDs,
		"force-push marker must record [oldHead, newHead] in commit_ids exactly: the timeline renderer parses these two IDs to display the before/after compare; got %v",
		data.CommitIDs)
}

// TestVerifyForcePushNewCommentIncludesKeptReachableCommit covers the
// `force_push_new_comment_lists_commits_from_merge_base` criterion
// (fail_to_pass): it pins the second defect — the new-commit list for a
// force-push must be computed against `mergeBase(base, newHead)..newHead`, NOT
// `oldHead..newHead`. Unlike the kept-non-force-comment test, this seeds NO
// prior comments, so a kept commit that is reachable from the new tip but lies
// OUTSIDE `oldHead..newHead` can only appear in the new push comment if the
// range start point is correct. This isolates the range arithmetic from however
// a solution reconciles pre-existing comments.
//
// Uses oldRef == newRef == headCommit (a rebase/amend that left the tip
// unchanged): `oldHead..newHead` is empty, so the pre-fix code records no
// normal-push comment at all and the kept parent commit vanishes — the headline
// "header shows 2 commits, timeline shows 1" symptom.
func TestVerifyForcePushNewCommentIncludesKeptReachableCommit(t *testing.T) {
	pusher, pr, gitRepo := verifyLoadFixturePR(t)

	headCommit, err := gitRepo.GetBranchCommit(pr.HeadBranch)
	require.NoError(t, err)
	require.Greater(t, headCommit.ParentCount(), 0,
		"this scenario requires the fixture head branch to have at least one parent commit; got 0 parents")
	parentCommit, err := headCommit.Parent(0)
	require.NoError(t, err, "must be able to load headCommit's parent")
	keptCommitID := parentCommit.ID.String()

	require.NoError(t, db.TruncateBeans(t.Context(), &issues_model.Comment{}))

	// Force-push with oldRef == newRef == headCommit. The pre-fix range
	// oldHead..newHead is empty, so the buggy code records no normal-push
	// comment and the kept parent commit never reaches the timeline. The fix
	// computes mergeBase(base, newHead)..newHead, which includes the parent.
	_, err = verifyCallCreatePushPullComment(
		t.Context(), pusher, pr,
		headCommit.ID.String(), headCommit.ID.String(), true,
	)
	require.NoError(t, err)

	comments := verifyFindPushComments(t, pr)
	keptCommitVisible := false
	nonForceComments := 0
	for _, c := range comments {
		data := verifyParsePushActionContent(t, c)
		if data.IsForcePush {
			continue
		}
		nonForceComments++
		for _, id := range data.CommitIDs {
			if id == keptCommitID {
				keptCommitVisible = true
				break
			}
		}
	}
	assert.True(t, keptCommitVisible,
		"after a force-push (no prior comments seeded), commit %s — a parent of the new tip, reachable from it but OUTSIDE oldHead..newHead — MUST appear in the new normal-push comment so the timeline lists every commit on the branch; pre-fix computes the new commit list from oldHead..newHead (empty here) so the commit is dropped, which is the headline 'header shows 2 commits, timeline shows 1' symptom; non-force-push comment count=%d, total push comments=%d",
		keptCommitID, nonForceComments, len(comments))
}
