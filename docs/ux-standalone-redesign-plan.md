# Standalone downloader UX redesign — implementation plan

## Goal
Make the existing standalone Core dashboard usable by a nontechnical person: paste a link, inspect the detected content, make a small number of meaningful choices, start a download, see what happened, and find the verified output.

## Reuse and boundaries
- Reuse current React/Vite UI, Core preview API, job queue, job-state contract, JobCard, settings and routing machinery. Do not re-implement download logic, routing, or upload.
- Keep Nextcloud users in Nextcloud native UI; never redirect Nextcloud users to standalone Core.
- The standalone API key is administrative access; ordinary end users must not gain administrative capabilities. Prototype uses mock data and is not an auth implementation.
- Do not modify Nextcloud ExApp or production configuration in this UX change.
- Default UI language for the software: English. Prepare for existing i18n infrastructure and RTL; do not introduce new languages without scope.
- This document is a design plan; browser review of prototype is a gate before React implementation.

## Users and jobs to be done
1. Regular permitted user: paste one URL, identify what was found, pick basic output settings, queue a job, locate the completed file.
2. User with playlist: choose items and see total selected count and size when known; distinguish unknown sizes without guesses.
3. Operator: review active/failed/cancelled work, retry only failed items, inspect detailed errors as a secondary action.
4. Administrator: configure connections, policies, engine options and diagnostic tools in a separate area, never on the basic download journey.

## Information architecture
- Download (default): one primary input and action; detected preview; common choices only.
- My downloads: progress and verified file/open-folder link, filters; empty state offers New download.
- Settings: personal/default output options.
- Administration (privileged): engines, routing, credentials, diagnostics. Do not render Core token controls for regular users.

## Link-first journey
1. Ready: a URL field with Paste and example hint. Enter / click Inspect; invalid/unsupported links explain next step.
2. Inspecting: visible working state, preserve URL. Do not show an unready Download button.
3. Detected: title/source, thumbnail or fallback, kind; quality/source options via typed controls; playlist selection with total size when known.
4. Queue: one primary Download action; accepted means Queued (not delivered). Always show route to My downloads.
5. Active/completed/failed/cancelled: accurate status, progress, cancel/retry, verified file access; technical log under Details.

## UI standards
- Progressive disclosure: engine arguments, routing overrides, raw JSON and connection details never appear in the common flow.
- Full keyboard use, visible focus, appropriate touch targets, screen-reader labels; reduced motion.
- Desktop: centered form column plus restrained queue preview; mobile: single column and full task functionality.
- Error messages must separate invalid link, unsupported source, lack of connection, download failure, delivery failure.
- Empty/unauthorized/disconnected/loading/partial states are designed, not blank.
- Use the existing theme; do not introduce a second settings authority.
- Final URL scheme acceptance must come from a shared backend capability contract, not UI guesses (#185).

## Acceptance criteria
- Nontechnical test operator can complete one link→preview→download→verified file flow without opening Advanced.
- Same core tasks work at 360 px mobile and desktop viewport, by touch and keyboard.
- A cancelled download cannot be retried by Retry all failed; status never implies verified delivery before the completion record.
- Live API/browser checks cover unsuccessful network/preview and partial/failed delivery states.
- Existing Nextcloud user flow and Core admin controls remain unaffected.
- Implementation follows owner review of the live-clickable prototype.

## Delivery order
1. Standalone HTML prototype with mock data: prototypes/standalone-link-first.html.
2. Owner evaluates click-flow and mobile widths, confirms or changes design.
3. Extract reusable React components from the existing App, preserve its stores, endpoint contracts and tests.
4. Add browser interaction coverage, accessibility checks and real disposable download→delivery acceptance.
5. No production rollout until actual-target verification and approval.

## Explicit limits of this prototype
Mock content, progress, and download links are simulated; no API requests, credentials, real downloads or data persistence. It demonstrates layout and interactions only and must not be accepted as proof of service functionality.
