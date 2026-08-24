# Live GitHub Draft PR demonstration

This runbook proves KubeFit's final GitOps handoff in a deliberately disposable
GitHub repository. It is an operator procedure, not an automated test: every
external mutation is visible, named, and separately invoked.

## Safety boundary

Do not use `origin` or a production GitOps repository. The procedure creates a new
private repository, pushes the current base branch, creates one KubeFit branch and
Draft PR, repeats publication to prove reuse, then archives the repository by
default. It never merges or deploys the PR.

Prerequisites:

- A clean KubeFit checkout on `main` with the source manifest at the proposal path.
- One immutable proposal and one referencing benchmark result with verdict `pass`.
- `gh`, `git`, `jq`, and the KubeFit virtual environment installed.
- A GitHub account allowed to create a private repository and Draft PR.
- Git authentication usable by `git push`; GitHub API authentication usable by `gh`.

The examples assume Bash and must be run from the KubeFit Git top-level. Choose a
unique repository name and keep all variables in the same shell session:

```bash
set -euo pipefail

KUBEFIT_DEMO_OWNER="your-github-login"
KUBEFIT_DEMO_REPO="kubefit-live-demo-20260821"
KUBEFIT_DEMO_REPOSITORY="${KUBEFIT_DEMO_OWNER}/${KUBEFIT_DEMO_REPO}"
KUBEFIT_DEMO_REMOTE="kubefit-live-demo"
KUBEFIT_PROPOSAL=".kubefit/proposals/proposal-<digest>"
KUBEFIT_BENCHMARK="benchmarks/results/benchmark-<digest>"
KUBEFIT_BENCHMARK_PAIR="benchmarks/pairs/benchmark-pair-<digest>"
KUBEFIT_EVIDENCE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/kubefit-live-evidence.XXXXXX")"

test "$(git branch --show-current)" = "main"
test -z "$(git status --porcelain --untracked-files=all)"
test "$KUBEFIT_DEMO_REMOTE" != "origin"
git remote get-url "$KUBEFIT_DEMO_REMOTE" >/dev/null 2>&1 && {
  echo "demo remote already exists; stop and inspect it" >&2
  exit 1
}
```

The evidence directory is outside the repository so JSON capture cannot make the
checkout dirty and invalidate KubeFit's repository checks.

## 1. Restore authentication deliberately

Inspect the account before changing authentication:

```bash
gh auth status
```

If it is invalid, authenticate interactively with the intended account. Do not put a
token literal in the command or shell history:

```bash
gh auth login --hostname github.com
gh auth status
```

This runbook does not prescribe a broad token scope. The selected credential must
be able to create the disposable repository and Draft PR; Git push authentication
must also work. Organization SSO or policy may require additional approval.

## 2. Create and seed the disposable repository

Review the exact identity, then create an empty private repository linked under a
non-`origin` remote name:

```bash
printf 'Disposable target: %s\n' "$KUBEFIT_DEMO_REPOSITORY"
gh repo create "$KUBEFIT_DEMO_REPOSITORY" \
  --private \
  --description "Disposable KubeFit Draft PR verification" \
  --disable-issues \
  --disable-wiki \
  --source=. \
  --remote="$KUBEFIT_DEMO_REMOTE"

KUBEFIT_DEMO_REMOTE_URL="$(git remote get-url "$KUBEFIT_DEMO_REMOTE")"
case "$KUBEFIT_DEMO_REMOTE_URL" in
  "https://github.com/${KUBEFIT_DEMO_REPOSITORY}.git"|"git@github.com:${KUBEFIT_DEMO_REPOSITORY}.git") ;;
  *)
    echo "unexpected demo remote URL: $KUBEFIT_DEMO_REMOTE_URL" >&2
    exit 1
    ;;
esac
git push "$KUBEFIT_DEMO_REMOTE" HEAD:refs/heads/main
```

Do not use `--push` on `gh repo create`; pushing the explicit base ref makes the
seeded state reviewable and avoids publishing unrelated local branches.

## 3. Require a clean preflight

`publish-check` prints JSON on both success and failure. Exit 0 means no observable
blocker; exit 2 means the JSON is a valid blocked report. With `set -e`, the next
step cannot run after a blocked check:

```bash
GITHUB_TOKEN="$(gh auth token)" .venv/bin/kubefit publish-check \
  --proposal "$KUBEFIT_PROPOSAL" \
  --benchmark "$KUBEFIT_BENCHMARK" \
  --benchmark-pair "$KUBEFIT_BENCHMARK_PAIR" \
  --repository-root . \
  --remote "$KUBEFIT_DEMO_REMOTE" \
  > "$KUBEFIT_EVIDENCE_DIR/preflight.json"

jq -e '
  .status == "ready" and
  .mutation_performed == false and
  (.blockers | length) == 0
' "$KUBEFIT_EVIDENCE_DIR/preflight.json"
```

Stop if the report is blocked. Do not bypass a branch collision, stale artifact,
dirty checkout, repository identity error, or authentication failure.

## 4. Publish twice

The first run must create the remote branch and Draft PR:

```bash
GITHUB_TOKEN="$(gh auth token)" .venv/bin/kubefit publish \
  --proposal "$KUBEFIT_PROPOSAL" \
  --benchmark "$KUBEFIT_BENCHMARK" \
  --benchmark-pair "$KUBEFIT_BENCHMARK_PAIR" \
  --repository-root . \
  --remote "$KUBEFIT_DEMO_REMOTE" \
  --confirm-publish \
  > "$KUBEFIT_EVIDENCE_DIR/first-publish.json"

jq -e '
  .draft == true and
  .branch_reused == false and
  .pull_request_reused == false
' "$KUBEFIT_EVIDENCE_DIR/first-publish.json"
```

Run the exact command again. The second run must reuse both objects:

```bash
GITHUB_TOKEN="$(gh auth token)" .venv/bin/kubefit publish \
  --proposal "$KUBEFIT_PROPOSAL" \
  --benchmark "$KUBEFIT_BENCHMARK" \
  --benchmark-pair "$KUBEFIT_BENCHMARK_PAIR" \
  --repository-root . \
  --remote "$KUBEFIT_DEMO_REMOTE" \
  --confirm-publish \
  > "$KUBEFIT_EVIDENCE_DIR/second-publish.json"

jq -e '
  .draft == true and
  .branch_reused == true and
  .pull_request_reused == true
' "$KUBEFIT_EVIDENCE_DIR/second-publish.json"
```

## 5. Verify independent GitHub evidence

Do not trust only KubeFit's returned flags. Compare identifiers across both results,
read the remote ref independently, and ask GitHub for the PR state:

```bash
KUBEFIT_FIRST_SHA="$(jq -r .commit_sha "$KUBEFIT_EVIDENCE_DIR/first-publish.json")"
KUBEFIT_SECOND_SHA="$(jq -r .commit_sha "$KUBEFIT_EVIDENCE_DIR/second-publish.json")"
KUBEFIT_FIRST_PR="$(jq -r .pull_request_number "$KUBEFIT_EVIDENCE_DIR/first-publish.json")"
KUBEFIT_SECOND_PR="$(jq -r .pull_request_number "$KUBEFIT_EVIDENCE_DIR/second-publish.json")"
KUBEFIT_BRANCH="$(jq -r .branch "$KUBEFIT_EVIDENCE_DIR/first-publish.json")"

test "$KUBEFIT_FIRST_SHA" = "$KUBEFIT_SECOND_SHA"
test "$KUBEFIT_FIRST_PR" = "$KUBEFIT_SECOND_PR"

git ls-remote --refs "$KUBEFIT_DEMO_REMOTE" "refs/heads/$KUBEFIT_BRANCH" \
  > "$KUBEFIT_EVIDENCE_DIR/remote-ref.txt"
test "$(cut -f1 "$KUBEFIT_EVIDENCE_DIR/remote-ref.txt")" = "$KUBEFIT_FIRST_SHA"

gh pr view "$KUBEFIT_FIRST_PR" \
  --repo "$KUBEFIT_DEMO_REPOSITORY" \
  --json number,url,state,isDraft,headRefName,headRefOid,baseRefName,title,changedFiles \
  > "$KUBEFIT_EVIDENCE_DIR/github-pr.json"

jq -e --arg branch "$KUBEFIT_BRANCH" --arg sha "$KUBEFIT_FIRST_SHA" '
  .state == "OPEN" and
  .isDraft == true and
  .headRefName == $branch and
  .headRefOid == $sha and
  .baseRefName == "main" and
  .changedFiles == 1
' "$KUBEFIT_EVIDENCE_DIR/github-pr.json"

printf 'Evidence directory: %s\n' "$KUBEFIT_EVIDENCE_DIR"
```

The evidence set is complete only when all five files exist and the assertions pass:

```text
preflight.json
first-publish.json
second-publish.json
remote-ref.txt
github-pr.json
```

Copy these secret-free files to the eventual benchmark/demo evidence location only
after inspecting them. They intentionally contain identifiers and URLs but no token.

Finally, bind the exact five-file set back to the immutable proposal, primary
benchmark, and benchmark pair:

```bash
.venv/bin/kubefit verify-publication \
  --proposal "$KUBEFIT_PROPOSAL" \
  --benchmark "$KUBEFIT_BENCHMARK" \
  --benchmark-pair "$KUBEFIT_BENCHMARK_PAIR" \
  --evidence-dir "$KUBEFIT_EVIDENCE_DIR" \
  > "${KUBEFIT_EVIDENCE_DIR}.verified.json"
```

The verification result is deliberately written next to, not inside, the evidence
directory. The verifier rejects missing and additional files, recalculates every
SHA-256, rebuilds the pull request plan, and emits a deterministic
`publication-<digest>` ID. Preserve the five-file directory and its sibling result
together.

## 6. Preserve or explicitly remove the target

Archiving is the default because it retains reviewer-visible evidence while stopping
further writes:

```bash
gh repo archive "$KUBEFIT_DEMO_REPOSITORY" --yes
git remote remove "$KUBEFIT_DEMO_REMOTE"
```

If policy requires deletion, inspect the exact name again and run this separately.
Deletion is irreversible and requires GitHub's `delete_repo` authorization:

```bash
printf 'Permanent deletion target: %s\n' "$KUBEFIT_DEMO_REPOSITORY"
gh repo delete "$KUBEFIT_DEMO_REPOSITORY" --yes
git remote remove "$KUBEFIT_DEMO_REMOTE"
```

Never run both archive and delete paths. Deleting the GitHub repository does not
delete the external evidence directory; manage that evidence according to project
policy.
