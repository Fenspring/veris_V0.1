# Connector notes

One document per connector, recording what was actually true about the vendor
rather than what their marketing page said.

| Connector | Status | Notes |
|---|---|---|
| [ecfr](ecfr.md) | **unverified** | Real code against a public API, never run against it. See the note for what verification must settle |
| [file_import](file_import.md) | available | CSV today; SFTP and scheduled drops use the same shape |
| [mock](mock.md) | available | Demo LMS, policy system and standards feed |
| [healthstream](healthstream.md) | planned | Requires vendor enablement |

`available` means somebody ran it against the real system and recorded what
passed — see [verification/](verification/). `unverified` means the code is
complete and nobody has. The distinction is carried in the registry, the API and
the Connection Center, not just in this table.

Each note should cover: authentication, what the API actually exposes, rate
limits, pagination, change detection, sandbox availability, and anything that
cost time. Write down what was surprising — the next person integrating that
vendor is the audience.
