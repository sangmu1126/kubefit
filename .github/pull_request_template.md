## Problem

What user, operational, correctness, or maintenance problem does this change address?

## Change

What changed, and which alternatives or boundaries shaped the implementation?

## Evidence

List exact commands and results. Separate projections, controlled-demo observations,
and production evidence.

- [ ] Relevant tests added or updated
- [ ] `.venv/bin/ruff check .`
- [ ] `.venv/bin/pytest -q`
- [ ] Dashboard, Helm, and Docker checks run when affected

## Safety and compatibility

- [ ] No credentials, production metrics, generated local evidence, or identifying workload data included
- [ ] Recommendation and observation paths remain read-only
- [ ] Benchmark mutation restores the original workload on every handled exit path
- [ ] GitHub publication remains Draft-only and does not merge or deploy
- [ ] Persisted schema, artifact identity, package, and upgrade implications documented

## Documentation

- [ ] User or contributor documentation updated when behavior changed
- [ ] Development-journal entry added or updated for a meaningful engineering slice
- [ ] Remaining limitations and unsupported claims stated explicitly
