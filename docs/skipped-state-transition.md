# Durable skipped-delivery state

This is the canonical Core job lifecycle extension for Nextcloud collision-skip
behavior (Nextcloud issue #164). The Core owns the state machine; Nextcloud is
only a consumer of the generated contract.

| Current state | Next state | Writer | Side effects |
| --- | --- | --- | --- |
| delivering | completed | JobWorker via transition_job | set progress.state, percent=100, finished_at and verified output |
| delivering | skipped | JobWorker via transition_job | set progress.state='skipped', percent=100, finished_at and skipped output record; log existing-file preservation |
| skipped | queued | explicit retry/redelivery via requeue_job | clear finished_at, reset job progress to queued, preserve only verified manifest for redelivery |
| completed | queued | explicit retry/redelivery via requeue_job | same reset discipline |
| any other transition into skipped | rejected | transition_job | no mutation |

Terminal states: completed, skipped, failed, cancelled. Active states exclude
all four. Engine progress callbacks may *not* set skipped; only the verified
delivery completion classifier can do so. There is one authoritative Python
state vocabulary, exported to the standalone UI by the existing generated
TypeScript contract and to the Nextcloud PHP/JS/Python consumer by the
downstream contract generator.

A mixed delivery with at least one skipped collision has final status skipped;
its output file manifest preserves each item's own verified/skipped flags. A
skip must never claim a file was newly uploaded or verified merely because
the target already existed. A skipped job may be re-delivered only when its
original completed-download workspace manifest is still verified.

Verification: unit transition/DB-restart tests; collision-policy tests;
generated-contract checks; disposable Nextcloud WebDAV and browser gates.
This does not certify the actual Nextcloud production host.
