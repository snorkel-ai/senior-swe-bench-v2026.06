// Copyright 2026 The Gitea Authors. All rights reserved.
// SPDX-License-Identifier: MIT

// External-package (repository_test) contract test for the unadopted-
// repository listing public-API change: ListUnadoptedRepositories must
// return int64 for the count (was int pre-fix).
//
// services/repository/main_test.go declares `package repository` and
// calls unittest.MainTest in TestMain. The external `_test` package
// can coexist with that internal test main — both compile into the
// same test binary and share the TestMain. unittest.MainTest requires
// /repo/gitea/tests/sqlite.ini, which is baked into the image, plus
// the sqlite + sqlite_unlock_notify build tags (set in verify.toml).

package repository_test

import (
	"context"
	"testing"

	"code.gitea.io/gitea/models/db"
	repo_service "code.gitea.io/gitea/services/repository"
)

// TestListUnadoptedReturnsInt64 covers the
// `list_unadopted_returns_int64` criterion (fail_to_pass).
//
// The second return value of ListUnadoptedRepositories must be int64.
// Pre-fix it was int, so the function-value assignment below fails to
// compile against pre-fix code with a build error like:
//
//	cannot use repo_service.ListUnadoptedRepositories (value of type
//	func(ctx context.Context, query string, opts *db.ListOptions)
//	  ([]string, int, error))
//	as func(context.Context, string, *db.ListOptions)
//	  ([]string, int64, error) value in variable declaration
//
// Post-fix the assignment compiles. We do not invoke the function —
// calling it requires a fully-loaded fixture database, and the
// signature itself is the spec.
func TestListUnadoptedReturnsInt64(t *testing.T) {
	var fn func(context.Context, string, *db.ListOptions) ([]string, int64, error) = repo_service.ListUnadoptedRepositories
	if fn == nil {
		t.Fatal("ListUnadoptedRepositories is nil")
	}
}
