# Version 0.2 review and follow-up

Date: 2026-10-24. Scope: the uv migration, English Textual workspace, read-only
catalog additions, CI, tests and documentation. The review compared the staged
v0.2 tree with local v0.1 baseline `7826ec4bba917405d639531c1e7b272fb560450a`.
The reviewed diff covered 24 files; no untracked source was excluded.

## Completed review

The independent review completed with **Ready with fixes** and two retained P2
findings. A separate validator confirmed both against the source and evidence.
The completed receipt has status `complete`, run ID
`20261024-052133-c85ddf09`.

Ten agent perspectives completed: correctness, project standards, testing,
maintainability, CLI accessibility, security, public CLI/dictionary contracts,
reliability, adversarial failure sequences, and asynchronous UI races. The
reviewers inspected source and focused headless behavior; no car was involved.
No separate peer-review CLI was available, so these passes do not establish
different-provider or different-model validation.

Before this review, three simplification perspectives examined reuse, quality
and efficiency. The accepted change reduced the snapshot list to display
metadata instead of retaining full captures and recounting their records on
every search. The focused UI suite passed after that change.

## Accepted findings

| # | Finding | Resolution and evidence |
|---|---|---|
| 1 | Bluetooth advertised names could carry terminal control sequences through Rich Text into the compositor. | Device-table cells now escape C0, DEL and C1 controls for display. Two mocked-scan regressions verify inert OSC/CSI output, preserved Unicode and literal markup-like text, unchanged discovery records, and exact UUID selection without connecting. |
| 2 | Manual edits to an armed plan lacked behavioral tests for consent invalidation. | Eight real keyboard-input cases verify that each edit-context change clears stale consent, confirmation and preview. Typing a replacement plan path preserves its text; ordinary edits clear the old path. A control case proves searches and confirmation typing preserve the loaded plan. No writes or transaction receipts occur. |

The first defect was independently reproduced in headless compositor output by
the security reviewer, validator and integration owner. The follow-up regression
also failed against the unfixed implementation before the production change.
The second finding is a coverage gap: the reviewed input-invalidation handler
was not identified as defective.

The testing review also identified a small snapshot-history gap. Follow-up
coverage includes a mixture of valid and malformed saved artifacts, failed
inspection and recovery to usable controls.

A longer follow-up test exposed a trace-view callback during Textual shutdown,
after its screen was removed. The callback now returns when the app is no longer
running, before reading or advancing its trace offset. A deterministic regression
failed before this guard and now verifies that a late callback neither raises
nor consumes evidence. Diagnostic ownership and runtime error handling are
unchanged.

## Follow-up verification

One TUI/test fix batch applied both findings; zero findings were skipped. The
snapshot-history testing gap is also closed. The final focused UI suite passed
all 24 tests, and scoped Ruff passed. The integration owner inspected the final
diff. Complete-suite and packaging results are recorded in
[VALIDATION-0.2.md](VALIDATION-0.2.md).

No retained software finding remains unresolved. The full independent review
was not rerun after these targeted fixes; their regression tests and the final
integration checks provide the follow-up evidence.

## Remaining physical limits

The review does not validate the user's Mac, Vgate adapter, installed ECU
software, live writes or persistence after an ignition cycle. The shipped
physical-vehicle dictionary remains read-only. Outgoing long ISO-TP,
SecurityAccess, verified CarPlay activation and firmware recovery remain outside
the implemented capabilities; see [CODEX_HANDOFF.md](CODEX_HANDOFF.md).

Hosted Linux/macOS CI is separate evidence available in the repository's
Actions tab. It exercises software and simulated diagnostic traffic, not a
physical Bluetooth adapter or vehicle.
