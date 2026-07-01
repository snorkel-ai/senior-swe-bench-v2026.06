// Copyright 2026 The Gitea Authors. All rights reserved.
// SPDX-License-Identifier: MIT

// External-package (context_test) contract test for the pagination
// public-API change: NewPagination and (*APIContext).SetLinkHeader must
// accept int64 for the "total" argument (was int pre-fix).
//
// Strategy: assign the function/method to a variable of the post-fix
// type. Go is strict about function-type assignments, so the file
// compiles only against the post-fix signature; pre-fix code yields a
// build error. Only exported names are touched.

package context_test

import (
	"testing"

	gitea_context "code.gitea.io/gitea/services/context"
)

// TestPaginationSignatureInt64 covers the
// `pagination_signature_int64` criterion (fail_to_pass).
//
// The first parameter of NewPagination must be int64. Pre-fix it was
// int, so the variable declaration below fails to compile against
// pre-fix code with a build error like:
//
//	cannot use gitea_context.NewPagination (value of type
//	func(total int, pagingNum int, current int, numPages int) *Pagination)
//	as func(int64, int, int, int) *Pagination value in variable declaration
//
// Post-fix the assignment compiles and the smoke call returns a
// non-nil paginator.
func TestPaginationSignatureInt64(t *testing.T) {
	var fn func(int64, int, int, int) *gitea_context.Pagination = gitea_context.NewPagination
	if fn == nil {
		t.Fatal("NewPagination is nil")
	}
	// Smoke call to make sure the function is reachable, not just typed.
	// total=0 keeps the underlying paginator on the trivial path.
	p := fn(int64(0), 10, 1, 0)
	if p == nil {
		t.Fatal("NewPagination returned nil for total=0")
	}
}

// TestSetLinkHeaderSignatureInt64 covers the
// `set_link_header_signature_int64` criterion (fail_to_pass).
//
// The "total" parameter of (*APIContext).SetLinkHeader must be int64.
// Pre-fix it was int, so the method-value assignment below fails to
// compile against pre-fix code. We do not invoke the method — calling
// it requires a fully-constructed APIContext (HTTP request, response
// writer, settings) which is unrelated to the contract under test.
// The compile-time discriminator alone is sufficient.
func TestSetLinkHeaderSignatureInt64(t *testing.T) {
	var fn func(*gitea_context.APIContext, int64, int) = (*gitea_context.APIContext).SetLinkHeader
	if fn == nil {
		t.Fatal("SetLinkHeader method-value is nil")
	}
}
