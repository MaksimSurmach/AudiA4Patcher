# Review and follow-up

Date: 2026-10-24. Scope: the new local Audi Workbench application, tests and
dictionary. The completed independent code review examined implementation
commit `dd93ea06c89241b91830ec67153151100f04f241` against the initial contract
commit `8d1689c853a4ccb3fc64d92b68725195be246ff8`.

## Method and result

Eight review perspectives completed: correctness, project standards, testing,
maintainability, security, reliability, adversarial failure analysis, and
asynchronous UI races. A separate validation pass confirmed all seven retained
findings. The initial verdict was **Ready with fixes**. Follow-up changes and
regression tests address all seven findings below; the final validation results
are in [VALIDATION.md](VALIDATION.md).

These were independent agent reviews within the available local environment.
No different-provider review CLI was available, so this is not a claim of
cross-model validation. Neither the review nor the automated tests used a car.

## Accepted findings and resolution

| # | Finding | Resolution and maintained evidence |
|---|---|---|
| 1 | A permitted edit could have an original enum value that the same definition could not restore. | Validate every declared enum in both the original and proposed value. Tests cover unknown original values, blocked previews, repeated validation at apply, zero writes on rejection, and a valid reverse change. |
| 2 | A connected-log persistence failure left the Workbench marked open after cleanup. | Mark the session open only after the connected event is saved. Reset failed entry, release the lock, and preserve the original error even if cleanup also fails. Re-entry and lock acquisition are tested. |
| 3 | Latest snapshot selection used random filename order within a second. | Sort by filesystem modification time in nanoseconds, with the filename as a deterministic tie-breaker. Tests cover inverted UUID names, updated artifacts, equal times, TUI startup and the actual Latest button. |
| 4 | A mixed NO DATA response could hide incomplete frames, pending responses or adapter errors. | Recover only from a sole normalized NO DATA status. Mixed responses invalidate and close the connection. Tests confirm that another request cannot reuse the ambiguous stream. |
| 5 | Journal failure boundaries and cancellation around a write lacked maintained tests. | Added transaction-creation and durable-intent failures, final-receipt failure, reconnect/reconciliation, and event-controlled cancellation during write response and readback. Tests distinguish zero writes before the boundary from exactly one write and an unresolved receipt after it. |
| 6 | BLE cleanup could wait indefinitely and prevent saving the unknown result. | Give notification shutdown and disconnect separate two-second cancellation deadlines, attempt disconnect in finally, retain the client during cleanup, and preserve the original error. Fake-backend integration verifies the unknown receipt and process-lock release. |
| 7 | Active TUI quit and duplicate-action guards lacked maintained tests. | Added event-held writes through actual Textual worker/button paths. Tests exercise quit keys, action_quit, duplicate dispatch, disabled controls, one write, successful completion, idle quit and reset after failure. No production UI defect was reproduced. |

The first four reproduced defects and the BLE cleanup defects were demonstrated
against the previous implementation before their production fixes. The journal
and TUI findings were coverage gaps; their tests verify existing guarded
behavior. Additional extended-session tests cover valid confirmation, rejection,
malformed responses, and a changed value, voltage, speed or RPM during the switch.

## Remaining engineering limits

- Hardware compatibility is still unverified. An actual read-only collection
  from the intended Mac, adapter and vehicle is the next source of evidence.
- Outgoing long ISO-TP is deliberately unsupported; the live transport rejects
  requests longer than seven UDS bytes. No review finding authorizes bypassing
  that boundary.
- No exact live feature map or BLE-writable definition for this MHS2 is shipped.
  Raw data and synthetic examples are not evidence of real coding semantics.
- Async cleanup deadlines depend on the backend cooperating with cancellation.
  The maintained fake-backend tests model cancellable pending operations; they
  do not prove recovery from an OS or interpreter deadlock.
- A stored configuration record cannot recover an unreachable or corrupted ECU.
  Immediate readback does not establish persistence after an ignition cycle.

Targeted follow-up changes were inspected and tested. The full independent
review was not rerun after those changes; the final test suite covers them.
