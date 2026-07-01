// Driven exclusively through the pre-existing exported local.FakePaginate
// (which does both the kube-cluster dedup and the pagination); references no
// symbol the change introduces, so any behaviourally-correct implementation
// passes regardless of how dedup is stored or named.
//
//   - TestVerifyKubeDedupScoped (fail_to_pass): scope-aware dedup keeps every
//     distinct (name, scope); same name+same scope collapses; equivalent-but-
//     differently-spelled scopes (a missing leading separator is purely
//     syntactic) collapse; pagination visits each distinct cluster exactly once
//     across every page size. Pre-fix dedup keys on name only, so same-named
//     clusters collapse across scopes and the expected counts fail.
//   - TestVerifyOtherResourceKindsPaginateExactlyOnce (pass_to_pass): nodes and
//     database servers still paginate exactly once (regression guard).

package local_test

import (
	"strconv"
	"testing"

	"github.com/stretchr/testify/require"

	apidefaults "github.com/gravitational/teleport/api/defaults"
	"github.com/gravitational/teleport/api/types"
	local "github.com/gravitational/teleport/lib/services/local"
)

// paginateAll walks the whole list once per page size, feeding each response's
// NextKey back as the next StartKey, and returns the distinct-identity count,
// the per-run returned count (asserted equal across page sizes), and whether
// every run was duplicate-free and yielded an identical set.
func paginateAll(
	t *testing.T,
	resources []types.ResourceWithLabels,
	resourceType string,
	kinds []string,
	identity func(types.ResourceWithLabels) string,
	pageSizes []int,
) (uniqueCount, totalReturned int, eachVisitedOnce bool) {
	t.Helper()
	eachVisitedOnce = true
	var baseline map[string]struct{}
	totalReturned = -1
	for k, pageSize := range pageSizes {
		var visited []string
		startKey := ""
		guard := 0
		for {
			resp, err := local.FakePaginate(resources, local.FakePaginateParams{
				ResourceType: resourceType,
				Kinds:        kinds,
				Limit:        int32(pageSize),
				StartKey:     startKey,
			})
			require.NoError(t, err)
			for _, r := range resp.Resources {
				visited = append(visited, identity(r))
			}
			startKey = resp.NextKey
			guard++
			require.Less(t, guard, 1000, "pagination did not terminate at page size %d", pageSize)
			if startKey == "" {
				break
			}
		}
		set := make(map[string]struct{}, len(visited))
		for _, v := range visited {
			set[v] = struct{}{}
		}
		if len(visited) != len(set) {
			eachVisitedOnce = false
		}
		if k == 0 {
			baseline = set
			totalReturned = len(visited)
		} else {
			if len(set) != len(baseline) || len(visited) != totalReturned {
				eachVisitedOnce = false
			}
		}
		uniqueCount = len(baseline)
	}
	return uniqueCount, totalReturned, eachVisitedOnce
}

func kubeIdentity(r types.ResourceWithLabels) string {
	kc := r.(types.KubeCluster)
	return kc.GetName() + "|" + kc.GetScope()
}

func buildKubeClusters(t *testing.T, names, scopes []string) []types.ResourceWithLabels {
	t.Helper()
	resources := make([]types.ResourceWithLabels, len(names))
	for i := range names {
		kc, err := types.NewKubernetesClusterV3(
			types.Metadata{Name: names[i], Revision: strconv.Itoa(i)},
			types.KubernetesClusterSpecV3{},
			types.KubeClusterWithScope(scopes[i]),
		)
		require.NoError(t, err)
		resources[i] = kc
	}
	return resources
}

func TestVerifyKubeDedupScoped(t *testing.T) {
	cases := []struct {
		name       string
		names      []string
		scopes     []string
		pageSizes  []int
		wantUnique int
		wantTotal  int
	}{
		{"three_distinct_scopes_all_kept", []string{"c", "c", "c"}, []string{"/aa", "/bb", "/cc"}, []int{1}, 3, 3},
		{"same_name_same_scope_collapses", []string{"c", "c", "c"}, []string{"/aa", "/aa", "/bb"}, []int{2}, 2, 2},
		{"eight_scopes_paginate_once", []string{"c", "c", "c", "c", "c", "c", "c", "c"}, []string{"/aa", "/bb", "/cc", "/dd", "/ee", "/ff", "/gg", "/hh"}, []int{1, 2, 3, 5, 8}, 8, 8},
		{"two_names_two_scopes", []string{"x", "x"}, []string{"/s1", "/s2"}, []int{1, 2}, 2, 2},
		{"four_clusters_two_name_pairs", []string{"x", "x", "y", "y"}, []string{"/s1", "/s2", "/s1", "/s2"}, []int{1, 3, 4}, 4, 4},
		{"equivalent_scopes_collapse", []string{"c", "c"}, []string{"/aa", "aa"}, []int{1, 2}, 1, 1},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			resources := buildKubeClusters(t, tc.names, tc.scopes)
			uniq, total, once := paginateAll(t, resources, types.KindKubernetesCluster, []string{types.KindKubernetesCluster}, kubeIdentity, tc.pageSizes)
			require.Equal(t, tc.wantUnique, uniq, "distinct (name,scope) count")
			require.Equal(t, tc.wantTotal, total, "total returned")
			require.True(t, once, "each distinct cluster visited exactly once across page sizes")
		})
	}
}

func TestVerifyOtherResourceKindsPaginateExactlyOnce(t *testing.T) {
	// Nodes: each node is its own contained resource, so the kind filter applies.
	nodeNames := []string{"n1", "n2", "n3", "n4", "n5", "n6", "n7"}
	nodes := make([]types.ResourceWithLabels, len(nodeNames))
	for i, name := range nodeNames {
		nodes[i] = &types.ServerV2{
			Kind:     types.KindNode,
			Version:  types.V2,
			Metadata: types.Metadata{Name: name, Namespace: apidefaults.Namespace},
			Spec:     types.ServerSpecV2{Hostname: "node"},
		}
	}
	uniq, total, once := paginateAll(t, nodes, types.KindNode, []string{types.KindNode},
		func(r types.ResourceWithLabels) string { return r.GetName() }, []int{1, 2, 3, 5, 7})
	require.Equal(t, 7, uniq)
	require.Equal(t, 7, total)
	require.True(t, once)

	// Database servers: the match path filters on the CONTAINED database, so
	// Kinds is left nil (a server-level kind filter would match nothing).
	dbNames := []string{"db1", "db2", "db3", "db4", "db5"}
	dbs := make([]types.ResourceWithLabels, len(dbNames))
	for i, name := range dbNames {
		db, err := types.NewDatabaseServerV3(
			types.Metadata{Name: name},
			types.DatabaseServerSpecV3{
				HostID:   "host-" + strconv.Itoa(i),
				Hostname: "hostname",
				Database: &types.DatabaseV3{
					Metadata: types.Metadata{Name: name},
					Spec: types.DatabaseSpecV3{
						Protocol: "postgres",
						URI:      "postgres://user:password@host",
					},
				},
			},
		)
		require.NoError(t, err)
		dbs[i] = db
	}
	uniq, total, once = paginateAll(t, dbs, types.KindDatabaseServer, nil,
		func(r types.ResourceWithLabels) string { return r.GetName() }, []int{1, 2, 5})
	require.Equal(t, 5, uniq)
	require.Equal(t, 5, total)
	require.True(t, once)
}
