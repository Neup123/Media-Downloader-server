# Feature image acceptance probe — PR #173

This branch is a temporary CI trigger based on exact Core feature head `7b24b5dc0e2af3b9436787a43f568d8b1af1dcf0`.

It changes no runtime, API or engine logic. A PR targeting `feat/install-all-engines` tests the feature branch's Docker image, runtime health, unauthenticated 401 and authenticated inventory via `.github/workflows/test.yml`; its existing live Nextcloud workflow also runs if triggered. CI containers and test data must be disposable and cleaned by the workflows.

**Not a release gate by itself:** new engine-specific live link-to-file delivery, Egress Guard, baseline reconciliation, and `musicdl` noncommercial-license eligibility remain open. Do not merge this probe into main. Record exact-head results in PR #173, then close the probe PR.
