# Demo connectors

**Status:** available · **Auth:** none · Labelled `demo data` everywhere.

Mock LMS, policy system and standards feed. They implement the same interface as
a real connector and pass the same contract tests, which is why a new real
connector is proven against a suite that already has passing implementations.

| | Discovery reports | Demo syncs |
|---|---|---|
| Demo Learning System | 827 courses · 12,842 staff · 14,203 completions | 40 · 200 · 400 |
| Demo Policy System | 14,284 policies | 6 |
| Demo Standards Feed | 3 requirements | 3 |

Discovery reports the true totals and sync respects the chosen scope. Reporting
14,284 and syncing 6 is honest behaviour and is what a real connector does when
the customer selects a scope.

The data is deliberately coherent with the knowledge corpus: the controlled
substance course was last revised before the standard changed, so the
policy-to-training agent finds genuine drift rather than manufactured noise.

**A mock is never presented as a live integration.**
