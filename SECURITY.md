# Security

Veris is designed for hospital networks. This describes what it does and does
not do. **Nothing here is a claim of HIPAA compliance.**

## Scope: no PHI

Veris connects administrative, regulatory, policy, training and operational
metadata. It does not integrate with EHRs and has no patient-facing function.
Staff rosters are the closest it comes to personal data, and they carry name,
role, department and facility — not clinical information.

If a connector would expose PHI, that is a reason to narrow the connector.

## Credentials

Credentials go to the operating system's secret store — Keychain, Credential
Manager/DPAPI, Secret Service — and nowhere else.

**There is deliberately no encrypted-file fallback.** A file Veris can decrypt
unattended is a file an attacker with the host can decrypt, and shipping one
would let an organization believe its LMS credentials are protected by something
stronger than filesystem permissions. When no OS store exists, the options are an
operator-supplied environment variable or an explicit refusal.

`/api/v1/health` and the Connection Center both report which store is in effect,
so nobody has to guess.

A contract test asserts that no credential reaches the database.

## Logging and error handling

- Errors are redacted before being logged or stored: API keys, tokens, bearer
  headers, passwords and secrets are stripped. A contract test covers this.
- Unhandled API errors return a generic message; the detail goes to the log.
- Document contents are never logged.

## API

- Every input is length- and type-bounded via Pydantic models.
- Uploads are size-limited (25 MB) and extension-restricted; the client filename
  is used only for its extension, after sanitisation, so it cannot escape a
  directory.
- `VERIS_API_TOKEN` guards every mutating route. When unset the API is open,
  which is correct for a local demo and wrong for anything else — health reports
  which posture is in effect.
- Read-only routes are unauthenticated by default; put the instance behind TLS
  and an authenticating proxy for multi-user deployments.

## Connectors

- Read-only by construction: no write method exists, and the registry rejects a
  connector declaring write capabilities.
- Each connector declares what it reads, shown to the customer before connecting.
- Rate limits are obeyed rather than retried through, so a customer's API access
  is not put at risk by Veris.

## Auditability

`events` records ingestion, connection, sync, agent runs and rejections.
`sync_runs` records every synchronization with counts and redacted errors.
`reviews` is an append-only log of human decisions. Every finding carries the
evidence spans it rests on and the scope it was established over.

## Deployment hardening

1. Set `VERIS_API_TOKEN`.
2. Terminate TLS in front of the container; validate certificates outbound.
3. Mount `/app/data` on durable, access-controlled storage — it holds the graph,
   the frozen canonical text every citation points into, and original artifacts.
4. Run the container unprivileged (the image already does).
5. Prefer a local model provider when policy text must not leave the network.

## Known limits

- Single-process SQLite: no row-level access control, and no multi-tenant
  isolation. A shared deployment needs one instance per organization.
- No user accounts or RBAC yet. The reviewer name on a review is supplied, not
  authenticated.
- The desktop shell's code signing, notarization and auto-update are configured
  but unexercised — see Decision 0007.
