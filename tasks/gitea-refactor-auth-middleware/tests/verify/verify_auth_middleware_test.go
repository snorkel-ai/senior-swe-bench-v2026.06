// Copyright 2026 The Gitea Authors. All rights reserved.
// SPDX-License-Identifier: MIT
//
// The behavior change exercised by the migration: the workflow status
// badge URL (pre-existing route, handler, and image/svg+xml response
// shape) now responds to Basic auth and OAuth2 personal access tokens,
// not just the browser session cookie.
//
// Discrimination point: pre-fix, the auth methods skip URLs that don't
// match a hardcoded path-detection regex set, and the badge URL isn't in
// any of them, so authenticated requests stay anonymous and the private
// repo returns 404. Post-fix, the route opts the request into the
// appropriate auth method, the credential is honoured, and the SVG serves.
//
// The test imports only pre-existing packages/helpers and never
// references task-introduced identifiers, so alternative implementations
// (different naming, layout, or per-route opt-in mechanism) pass as long
// as the badge-URL behaviour change is observable to HTTP callers.

package integration

import (
	"net/http"
	"testing"

	auth_model "code.gitea.io/gitea/models/auth"
	"code.gitea.io/gitea/models/db"
	repo_model "code.gitea.io/gitea/models/repo"
	"code.gitea.io/gitea/models/unit"
	"code.gitea.io/gitea/tests"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// TestVerifyAuthMiddlewareWorkflowBadge drives three sub-tests
// against the badge URL on user2/repo2 (a private repo per the fixture
// data). The anonymous case is a control — it MUST 404 both before
// and after the migration so we know the test setup actually requires
// authentication. The basic-auth and bearer-token cases are the
// fail-to-pass cases: they 404 on the pre-fix tree and 200 on the
// post-fix tree.
func TestVerifyAuthMiddlewareWorkflowBadge(t *testing.T) {
	defer tests.PrepareTestEnv(t)()

	// user2/repo2 is private (models/fixtures/repository.yml id=2,
	// is_private: true) but ships without a TypeActions repo_unit row
	// in the fixture data — only user2/repo1 (public) has one. The
	// MustEnableActions guard in the badge route returns 404 if the
	// unit isn't present, which would mask the auth signal we're
	// actually probing. Insert a minimal TypeActions unit row here
	// so the badge handler runs once auth has succeeded. Idempotent:
	// only inserts if absent so the test can run repeatedly under
	// the integration package's shared fixture pool.
	const repo2ID = int64(2)
	exists, err := db.GetEngine(t.Context()).
		Where("repo_id = ? AND type = ?", repo2ID, int(unit.TypeActions)).
		Exist(&repo_model.RepoUnit{})
	require.NoError(t, err, "checking for existing Actions repo_unit row")
	if !exists {
		_, err := db.GetEngine(t.Context()).Insert(&repo_model.RepoUnit{
			RepoID: repo2ID,
			Type:   unit.TypeActions,
		})
		require.NoError(t, err, "seeding Actions repo_unit row on user2/repo2")
	}

	// Pre-existing URL pattern (routers/web/web.go in the pre-fix tree
	// already declares this route under the actions group). The
	// workflow file name is arbitrary — getWorkflowBadge falls back to
	// a "no status" badge when no run exists, which is exactly the SVG
	// response we want to confirm a successful response shape on.
	url := "/user2/repo2/actions/workflows/none.yml/badge.svg"

	// Sanity check: anonymous request to a private repo must 404.
	// Both pre- and post-migration. If this fails, the test setup is
	// wrong (e.g. the repo isn't private, or RepoAssignment isn't
	// rejecting anonymous users), and the fail-to-pass signal below
	// would be meaningless.
	t.Run("anonymous-private-repo-404", func(t *testing.T) {
		req := NewRequest(t, "GET", url)
		MakeRequest(t, req, http.StatusNotFound)
	})

	// Fail-to-pass: pre-fix → 404 (Basic.parseAuthBasic skips the URL
	// because it doesn't match the path-detection regex set, leaving
	// the request anonymous and the private repo invisible).
	// Post-fix → 200 with the SVG response body.
	t.Run("basic-auth", func(t *testing.T) {
		req := NewRequest(t, "GET", url).AddBasicAuth("user2")
		resp := MakeRequest(t, req, http.StatusOK)
		assert.Contains(t, resp.Body.String(), "<svg",
			"response body should contain an SVG element")
		assert.Equal(t, "image/svg+xml", resp.Header().Get("Content-Type"),
			"Content-Type should mark the response as SVG")
	})

	// Fail-to-pass: pre-fix → 404 (OAuth2.Verify also skips
	// non-matching URLs); post-fix → 200 with SVG. Issuing the token
	// via getUserToken — a pre-existing helper that creates an
	// AccessToken with the given scope on user2's account — guarantees
	// the bearer carries enough scope to read the repo (which is all
	// MustEnableActions / repo permission checks need).
	t.Run("bearer-token", func(t *testing.T) {
		token := getUserToken(t, "user2", auth_model.AccessTokenScopeReadRepository)
		req := NewRequest(t, "GET", url).AddTokenAuth(token)
		resp := MakeRequest(t, req, http.StatusOK)
		assert.Contains(t, resp.Body.String(), "<svg",
			"response body should contain an SVG element")
		assert.Equal(t, "image/svg+xml", resp.Header().Get("Content-Type"),
			"Content-Type should mark the response as SVG")
	})
}
