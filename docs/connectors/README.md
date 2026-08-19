# Connector notes

One document per connector, recording what was actually true about the vendor
rather than what their marketing page said.

| Connector | Status | Notes |
|---|---|---|
| [file_import](file_import.md) | available | CSV today; SFTP and scheduled drops use the same shape |
| [mock](mock.md) | available | Demo LMS, policy system and standards feed |
| [healthstream](healthstream.md) | planned | Requires vendor enablement |

Each note should cover: authentication, what the API actually exposes, rate
limits, pagination, change detection, sandbox availability, and anything that
cost time. Write down what was surprising — the next person integrating that
vendor is the audience.
