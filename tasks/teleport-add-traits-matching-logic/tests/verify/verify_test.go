// No-regression guard for the user search rewrite.
//
// The trait-aware feature itself (the traits matcher and the search widening
// to trait keys/values) is the NEW behaviour and is rewarded end-to-end
// through the pre-existing ListUsers endpoint by the validation stories — so
// it is deliberately NOT re-asserted here. This file owns only the
// no-regression half: that widening the search did not break matching against
// the fields the search already covered (name, labels, roles), and that the
// pre-existing case-insensitive, substring matching of those fields is
// preserved.
//
// Roles are exercised here specifically because the end-to-end path cannot:
// creating a user through the gRPC service validates that every assigned role
// exists, so a role-search regression can only be observed at this unit level,
// constructing the user directly.
//
// Everything is observed through the PRE-EXISTING, STABLE public surface
// (&types.UserFilter{...}).Match(*types.UserV2). This file NEVER references
// task-introduced symbols (a MatchTraits method, a ContainsAll helper, etc.) —
// those are implementation choices. Only the observable Match outcome is
// asserted.
package types_test

import (
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/gravitational/teleport/api/types"
)

// newUserV2 builds a concrete *types.UserV2 through the pre-existing public
// constructor and returns it ready for UserFilter.Match.
func newUserV2(t *testing.T, name string) *types.UserV2 {
	t.Helper()
	u, err := types.NewUser(name)
	require.NoError(t, err)
	v2, ok := u.(*types.UserV2)
	require.True(t, ok, "types.NewUser should return *types.UserV2")
	return v2
}

// TestVerifySearchNoRegression pins that the (possibly-rewritten) free-text
// user search still matches the pre-existing fields — name, labels (keys and
// values), and roles — and preserves their case-insensitive, substring
// semantics. It does not assert the new trait-search behaviour (validation
// owns that). Passes on an unmodified tree and on a correct solution
// (pass_to_pass); fails any solution that regresses the existing search.
func TestVerifySearchNoRegression(t *testing.T) {
	user := newUserV2(t, "zeta-user")
	user.SetStaticLabels(map[string]string{"region": "emea"})
	user.SetRoles([]string{"auditor"})

	cases := []struct {
		name    string
		keyword string
		want    bool
	}{
		{name: "name exact", keyword: "zeta-user", want: true},
		{name: "name substring case-insensitive", keyword: "ZETA", want: true},
		{name: "label key", keyword: "region", want: true},
		{name: "label value exact", keyword: "emea", want: true},
		{name: "label value substring case-insensitive", keyword: "EME", want: true},
		{name: "role exact", keyword: "auditor", want: true},
		{name: "role substring case-insensitive", keyword: "AUDIT", want: true},
		{name: "matches nothing", keyword: "absent", want: false},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			filter := &types.UserFilter{SearchKeywords: []string{tc.keyword}}
			require.Equal(t, tc.want, filter.Match(user))
		})
	}
}
