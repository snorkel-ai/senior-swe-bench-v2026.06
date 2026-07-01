// Copyright 2026 The Gitea Authors. All rights reserved.
// SPDX-License-Identifier: MIT

// Internal-package (gitdiff) test. All tests exercise
// (*highlightCodeDiff).diffLineWithHighlight, the only public-package
// entry point that the surrounding rendering code (gitdiff.go's
// getDiffLineForRender) calls. Internal helpers modified by this task are
// deliberately not named here, so any valid alternative implementation
// may restructure them differently.

package gitdiff

import (
	"html/template"
	"strings"
	"testing"

	"code.gitea.io/gitea/modules/highlight"

	"github.com/stretchr/testify/assert"
)

// TestVerifyDiffTagWrapping covers the
// `diff_marker_wraps_full_token_replacement` criterion (fail_to_pass).
//
// When two highlighted code lines differ in the full content of one
// syntax-highlighted token (same outer class on both sides, different
// inner text), the diff output must place the added-code/removed-code
// span around the outer syntax span — not nested inside it. Multiple
// chroma classes and payloads are exercised so an implementation that
// hardcodes a single fixture string fails at least one case.
func TestVerifyDiffTagWrapping(t *testing.T) {
	cases := []struct {
		name    string
		codeA   string
		codeB   string
		wantDel string
		wantAdd string
	}{
		{
			name:    "keyword_class",
			codeA:   `x <span class="k">foo</span> y`,
			codeB:   `x <span class="k">bar</span> y`,
			wantDel: `x <span class="removed-code"><span class="k">foo</span></span> y`,
			wantAdd: `x <span class="added-code"><span class="k">bar</span></span> y`,
		},
		{
			name:    "name_class_different_payload",
			codeA:   `a <span class="nx">baz</span> b`,
			codeB:   `a <span class="nx">qux</span> b`,
			wantDel: `a <span class="removed-code"><span class="nx">baz</span></span> b`,
			wantAdd: `a <span class="added-code"><span class="nx">qux</span></span> b`,
		},
		{
			name:    "operator_class_alphanumeric_payload",
			codeA:   `p <span class="o">alpha1</span> q`,
			codeB:   `p <span class="o">alpha2</span> q`,
			wantDel: `p <span class="removed-code"><span class="o">alpha1</span></span> q`,
			wantAdd: `p <span class="added-code"><span class="o">alpha2</span></span> q`,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			hcdDel := newHighlightCodeDiff()
			outDel := hcdDel.diffLineWithHighlight(DiffLineDel, template.HTML(tc.codeA), template.HTML(tc.codeB))
			assert.Equal(t, tc.wantDel, string(outDel),
				"DiffLineDel must wrap the syntax span with removed-code, not nest inside it (case %s)", tc.name)

			hcdAdd := newHighlightCodeDiff()
			outAdd := hcdAdd.diffLineWithHighlight(DiffLineAdd, template.HTML(tc.codeA), template.HTML(tc.codeB))
			assert.Equal(t, tc.wantAdd, string(outAdd),
				"DiffLineAdd must wrap the syntax span with added-code, not nest inside it (case %s)", tc.name)
		})
	}
}

// TestVerifyMultiTokenPrefixAdditionPreservesStructure covers the
// `multi_token_prefix_addition_preserves_structure` criterion
// (fail_to_pass).
//
// When a prefix is added before each of two existing
// syntax-highlighted tokens (e.g., `bot.` before each of `xxx`,
// `yyy` after running them through chroma's Go lexer), the resulting
// diff output must keep every span balanced, the kept tokens must
// retain their highlighting, and each prefix-addition must be
// contained in its own added-code span.
func TestVerifyMultiTokenPrefixAdditionPreservesStructure(t *testing.T) {
	oldCode, _ := highlight.RenderCodeFast("a.go", "Go", `xxx || yyy`)
	newCode, _ := highlight.RenderCodeFast("a.go", "Go", `bot.xxx || bot.yyy`)

	hcd := newHighlightCodeDiff()
	out := hcd.diffLineWithHighlight(DiffLineAdd, oldCode, newCode)
	assert.Equal(t,
		`<span class="added-code"><span class="nx">bot</span><span class="p">.</span></span><span class="nx">xxx</span><span class="w"> </span><span class="o">||</span><span class="w"> </span><span class="added-code"><span class="nx">bot</span><span class="p">.</span></span><span class="nx">yyy</span>`,
		string(out),
		"adding `bot.` before each of `xxx`, `yyy` must produce well-formed HTML with each prefix wrapped in its own added-code span")
}

// TestVerifyHTMLEntityHandling covers the
// `html_entity_preserved_with_balanced_spans` criterion (fail_to_pass).
//
// When one side introduces a chroma-class span containing an HTML
// entity (`&amp;`) alongside surrounding spans, the diff output must
// preserve the entity intact AND produce balanced `<span>`/`</span>`
// counts. Pre-fix, the placeholder/extractor path can fragment the
// 5-char entity sequence (e.g. splitting `&amp;` into `&am` + `p;`
// across a wrapper boundary) and produce unbalanced span counts —
// both observable via behavioral assertions without constraining the
// specific nesting the implementation chooses.
func TestVerifyHTMLEntityHandling(t *testing.T) {
	hcd := newHighlightCodeDiff()
	codeA := template.HTML(`<span class="nx">xxx</span>`)
	codeB := template.HTML(`<span class="nx">bot</span><span class="o">&amp;</span><span class="nx">xxx</span>`)
	outAdd := hcd.diffLineWithHighlight(DiffLineAdd, codeA, codeB)
	outDel := hcd.diffLineWithHighlight(DiffLineDel, codeA, codeB)

	openAdd := strings.Count(string(outAdd), "<span")
	closeAdd := strings.Count(string(outAdd), "</span")
	assert.Equal(t, openAdd, closeAdd,
		"Add output has unbalanced <span>/</span> counts: open=%d close=%d, out=%q",
		openAdd, closeAdd, string(outAdd))
	openDel := strings.Count(string(outDel), "<span")
	closeDel := strings.Count(string(outDel), "</span")
	assert.Equal(t, openDel, closeDel,
		"Del output has unbalanced <span>/</span> counts: open=%d close=%d, out=%q",
		openDel, closeDel, string(outDel))

	assert.Contains(t, string(outAdd), "&amp;",
		"Add output must contain an intact &amp; entity, got: %q", string(outAdd))
	assert.Contains(t, string(outDel), "xxx",
		"Del output must contain the common xxx content, got: %q", string(outDel))
}

// TestVerifyPartialChangeInsideTokenStillNests covers the
// `partial_change_inside_token_still_nests` criterion (pass_to_pass).
//
// When the differing portion is a substring inside a wider syntax
// span, the diff marker must continue to nest inside the outer span.
// Guards against an over-correcting fix that always hoists the marker.
func TestVerifyPartialChangeInsideTokenStillNests(t *testing.T) {
	codeA := template.HTML(`<span class="cm">this is a comment</span>`)
	codeB := template.HTML(`<span class="cm">this is updated comment</span>`)

	hcd := newHighlightCodeDiff()
	outDel := hcd.diffLineWithHighlight(DiffLineDel, codeA, codeB)
	assert.Equal(t,
		`<span class="cm">this is <span class="removed-code">a</span> comment</span>`,
		string(outDel),
		"partial-change inside the cm span: removed-code must remain nested inside cm")

	hcd = newHighlightCodeDiff()
	outAdd := hcd.diffLineWithHighlight(DiffLineAdd, codeA, codeB)
	assert.Equal(t,
		`<span class="cm">this is <span class="added-code">updated</span> comment</span>`,
		string(outAdd),
		"partial-change inside the cm span: added-code must remain nested inside cm")
}
