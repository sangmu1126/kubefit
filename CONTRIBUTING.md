# Contributing to KubeFit

Thank you for helping make Kubernetes resource changes easier to explain,
reproduce, and review. KubeFit welcomes bug reports, documentation improvements,
new tests, and focused implementation changes.

## Before you start

- Search existing issues before opening a new one.
- Open an issue before a large behavioral or architectural change so the problem,
  evidence, and safety boundary can be agreed on first.
- Do not include cluster credentials, GitHub tokens, raw production metrics, local
  benchmark evidence, or identifying workload data in an issue or pull request.
- Keep HPA recommendations, multi-cloud pricing catalogs, predictive incident
  detection, Terraform generation, and an AI chatbot outside the current MVP unless
  the project scope is explicitly revised.

Questions and small documentation corrections can start directly as an issue or
pull request. Security reports follow [SECURITY.md](SECURITY.md), not the public issue
tracker.

## Development setup

KubeFit supports Python 3.12, 3.13, and 3.14. The reviewed dependency locks are the
source of truth for local and CI environments.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements/build.lock
.venv/bin/python -m pip install --require-hashes -r requirements/dev.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
npm --prefix dashboard ci
```

See [the local development guide](docs/local-development.md) before using kind,
Prometheus, k6, Docker, or the GitHub publication path. Those workflows have stricter
identity, restoration, and credential boundaries than unit tests.

## Quality gates

Run the smallest relevant checks while developing and the complete local suite before
requesting review:

```bash
.venv/bin/ruff check .
.venv/bin/pytest -q
npm --prefix dashboard test -- --run
npm --prefix dashboard run build
helm lint deploy/helm/kubefit
helm template kubefit deploy/helm/kubefit >/dev/null
docker build -t kubefit:contributor-check .
```

GitHub Actions repeats Python checks on all three supported Python versions and runs
Dashboard, Helm, and packaged-runtime Docker jobs independently.

## Design and safety rules

- Recommendation code reads Kubernetes and Prometheus state; it does not mutate a
  cluster.
- Kubernetes quantities, workload identity, observation coverage, and artifact
  identity must fail closed when ambiguous.
- Cost projections and measured performance are separate claims.
- A benchmark failure or incomplete campaign is evidence and must not be discarded to
  obtain a favorable result.
- Generated YAML changes remain minimal and stale-safe.
- GitHub publication creates or reuses a Draft PR; it does not approve, merge, or
  deploy the change.
- Dashboard code renders API-owned decisions and must not duplicate recommendation or
  verification policy.
- Generated local evidence under ignored benchmark and `.kubefit` directories must
  not enter source, wheel, image, or pull-request history.

The component boundaries and evidence flow are documented in
[docs/architecture.md](docs/architecture.md).

## Tests and development records

Every behavior change needs tests at the lowest useful boundary. Bugs should include a
regression test that fails before the fix. Changes to persisted schemas, artifact
identities, publication safety, or package contents also need negative/tamper cases.

Meaningful development slices update the
[development journal](docs/devlog/README.md). Record why the change matters, what was
selected or rejected, how it works, reproducible evidence, the safe claim, and the
next unresolved question.

## Pull requests

- Keep the change focused and separate implementation from follow-up documentation or
  release packaging when practical.
- Use imperative commit subjects, for example `fix: reject mismatched pod samples`.
- Complete the repository pull-request template.
- Link the issue or explain why no issue was needed.
- Report commands and results exactly; do not describe a controlled demo as production
  evidence.
- Call out compatibility, migration, security, and rollback implications.
- Do not rewrite an existing public release or historical benchmark artifact.

Maintainers may request a smaller change, additional evidence, or a development-journal
entry before merging. Participation in this project is governed by the
[Code of Conduct](CODE_OF_CONDUCT.md).
