// Behavioral verifier (positive path) for "allow fast-forward-only merge when
// signed commits are required".
//
// Complementary to the validation stories, which own the REJECTION matrix
// (rebase/squash still refused; ff-only/merge diverge from the generic signing
// error; API 405). This verifier owns the one behavior the stories cannot show:
// that a fast-forward-only merge actually SUCCEEDS — the base branch advances —
// when the PR head is already verified, even though the instance has no signing
// key. Pre-fix, this same merge is refused with "wont sign: nokey", so the test
// is fail_to_pass.
//
// The head is made verified WITHOUT an instance signing key (SIGNING_KEY =
// none) by signing the head commit with a user-owned SSH key registered to
// user1; gitea then verifies the commit against that key. This is the only way
// a head can be verified while the instance still cannot sign — exactly the
// scenario the fix targets.

package integration

import (
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	asymkey_model "code.gitea.io/gitea/models/asymkey"
	auth_model "code.gitea.io/gitea/models/auth"
	"code.gitea.io/gitea/models/db"
	git_model "code.gitea.io/gitea/models/git"
	issues_model "code.gitea.io/gitea/models/issues"
	repo_model "code.gitea.io/gitea/models/repo"
	"code.gitea.io/gitea/models/unittest"
	user_model "code.gitea.io/gitea/models/user"
	"code.gitea.io/gitea/modules/ssh"
	api "code.gitea.io/gitea/modules/structs"
	"code.gitea.io/gitea/services/forms"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestFastForwardOnlyMergeVerifiedHeadSucceeds(t *testing.T) {
	onGiteaRun(t, func(t *testing.T, giteaURL *url.URL) {
		session := loginUser(t, "user1")
		testRepoFork(t, session, "user2", "repo1", "user1", "repo1", "")
		token := getTokenForLoggedInUser(t, session, auth_model.AccessTokenScopeWriteRepository)

		user1 := unittest.AssertExistsAndLoadBean(t, &user_model.User{Name: "user1"})

		// 1. Generate an SSH key pair and register the PUBLIC key to user1 as a
		//    VERIFIED key. gitea only checks Verified keys when validating a
		//    commit's SSH signature (services/asymkey/commit.go
		//    parseCommitWithSSHSignature), and keys added through the normal API
		//    flow start unverified — so register via the model and force-verify,
		//    mirroring gitea's own services/asymkey/commit_test.go. The instance
		//    itself still has NO signing key (SIGNING_KEY = none): the head is
		//    verified purely by the user's own key, which is the point of the fix.
		tmpDir := t.TempDir()
		require.NoError(t, os.Chmod(tmpDir, 0o700))
		keyFile := filepath.Join(tmpDir, "signing")
		require.NoError(t, ssh.GenKeyPair(keyFile))
		pubKey, err := os.ReadFile(keyFile + ".pub")
		require.NoError(t, err)
		signingKey, err := asymkey_model.AddPublicKey(t.Context(), user1.ID, "ff-signing-key", strings.TrimSpace(string(pubKey)), 0, true)
		require.NoError(t, err)
		_, err = db.GetEngine(t.Context()).ID(signingKey.ID).Cols("verified").Update(&asymkey_model.PublicKey{Verified: true})
		require.NoError(t, err)

		// 2. Clone user1/repo1 over HTTP, create a branch whose head commit is
		//    SSH-signed by that key, and push it. git invokes ssh-keygen to sign,
		//    so the runtime image must provide openssh-client.
		cloneURL := *giteaURL
		cloneURL.User = url.UserPassword("user1", userPassword)
		cloneURL.Path = "/user1/repo1.git"

		work := t.TempDir()
		runGit := func(t *testing.T, dir string, args ...string) {
			t.Helper()
			cmd := exec.Command("git", args...)
			cmd.Dir = dir
			cmd.Env = append(os.Environ(), "GIT_TERMINAL_PROMPT=0", "GIT_CONFIG_NOSYSTEM=1")
			out, runErr := cmd.CombinedOutput()
			require.NoErrorf(t, runErr, "git %v failed:\n%s", args, out)
		}
		runGit(t, tmpDir, "clone", cloneURL.String(), work)
		runGit(t, work, "config", "user.name", user1.FullName)
		runGit(t, work, "config", "user.email", user1.Email)
		runGit(t, work, "config", "gpg.format", "ssh")
		runGit(t, work, "config", "user.signingkey", keyFile)
		runGit(t, work, "config", "commit.gpgsign", "true")
		runGit(t, work, "checkout", "-b", "verified-update")
		require.NoError(t, os.WriteFile(filepath.Join(work, "README.md"), []byte("Hello, verified head\n"), 0o644))
		runGit(t, work, "add", "README.md")
		runGit(t, work, "commit", "-S", "-m", "signed head commit")
		runGit(t, work, "push", "origin", "verified-update")

		// 3. Open the PR and require signed commits on master (instance keyless).
		createPRReq := NewRequestWithJSON(t, http.MethodPost, "/api/v1/repos/user1/repo1/pulls", &api.CreatePullRequestOption{
			Head:  "verified-update",
			Base:  "master",
			Title: "ff-only merge with a verified head",
		}).AddTokenAuth(token)
		session.MakeRequest(t, createPRReq, http.StatusCreated)

		repo1 := unittest.AssertExistsAndLoadBean(t, &repo_model.Repository{OwnerID: user1.ID, Name: "repo1"})
		pr := unittest.AssertExistsAndLoadBean(t, &issues_model.PullRequest{
			HeadRepoID: repo1.ID, BaseRepoID: repo1.ID, HeadBranch: "verified-update", BaseBranch: "master",
		})
		require.NoError(t, git_model.UpdateProtectBranch(t.Context(), repo1, &git_model.ProtectedBranch{
			RepoID:               repo1.ID,
			RuleName:             "master",
			RequireSignedCommits: true,
		}, git_model.WhitelistOptions{}))

		// 4. Fast-forward-only merge must now SUCCEED (HTTP 200): no gitea commit
		//    is created and the verified head satisfies the protection. Pre-fix,
		//    this same call is refused with "wont sign: nokey".
		prIndex := strconv.FormatInt(pr.Index, 10)
		mergeReq := NewRequestWithJSON(t, http.MethodPost,
			"/api/v1/repos/user1/repo1/pulls/"+prIndex+"/merge",
			&forms.MergePullRequestForm{Do: string(repo_model.MergeStyleFastForwardOnly)},
		).AddTokenAuth(token)
		session.MakeRequest(t, mergeReq, http.StatusOK)

		// 5. Behavioral proof the FF actually happened: master now points at the
		//    verified head commit (branch advanced, no new merge commit).
		headBranch := unittest.AssertExistsAndLoadBean(t, &git_model.Branch{RepoID: repo1.ID, Name: "verified-update"})
		baseBranch := unittest.AssertExistsAndLoadBean(t, &git_model.Branch{RepoID: repo1.ID, Name: "master"})
		assert.Equal(t, headBranch.CommitID, baseBranch.CommitID,
			"fast-forward-only merge must advance master to the verified head commit")
	})
}
