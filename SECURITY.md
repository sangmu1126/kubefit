# Security policy

## Supported versions

Security fixes target the latest published release and the current `main` branch.
Older releases are immutable evidence boundaries and are not updated in place.

| Version | Supported |
|---|---|
| 0.3.x | Yes |
| 0.2.x and earlier | No |

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability or include live credentials,
production metrics, or identifying cluster data in a report. Contact the maintainer
privately through a contact method listed on the
[maintainer's GitHub profile](https://github.com/sangmu1126).

Include, when available:

- affected version or commit;
- affected component and deployment mode;
- reproducible steps using non-sensitive sample data;
- expected and observed impact;
- any proposed mitigation or patch.

The maintainer will acknowledge receipt, assess scope, coordinate a fix and disclosure
timeline, and credit the reporter if requested. Exact response times are not promised
while the project is maintained by an individual.

## Important boundaries

- KubeFit does not require a Kubernetes token by default. Optional observation RBAC is
  namespace-scoped and read-only.
- GitHub credentials remain in the caller's environment or credential helper; KubeFit
  does not persist them.
- The recommendation path is read-only. Benchmark and publication commands are
  explicit operator actions with restoration and Draft-only boundaries.
- Published SBOMs are inventory evidence, not a vulnerability scan, signature, or
  provenance attestation.

These boundaries reduce exposure but do not replace deployment-specific threat
modeling, network policy, secret management, or image vulnerability scanning.
