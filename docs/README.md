# Downloader documentation

Choose the guide for the work you are doing.

| Audience | Start here |
| --- | --- |
| Someone installing or upgrading the standalone Core service | [Install, upgrade and acceptance](installation-acceptance.md) |
| Someone downloading files or solving failed jobs | [Daily operator guide](operator-guide.md) |
| Someone reviewing security limits before production | [Egress guard](../EGRESS_GUARD.md) and [acceptance ledger](../ACCEPTANCE_STATUS.md) |
| Someone working with Core engines and update candidates | [Engine update guide](../ENGINE_UPDATES.md) |
| Someone working on the separate Nextcloud plugin | [Nextcloud integration](https://github.com/Neup123/downloader-server-nextcloud/blob/main/NEXTCLOUD_PER_USER_INSTALL.md) |
| A developer working against the API | [OpenAPI](../openapi.yaml) and local `/api-reference` |

The [blueprint](../BLUEPRINT.md) records historical design and desired acceptance. The current implementation, tests, release gates and remaining open issues take precedence over old phase descriptions.

**Product boundary:** This repository owns the standalone download server UI and execution backend. The Nextcloud repository owns only the native Nextcloud plugin surfaces. API data/state contracts can be shared; no browser UI is copied between products.

No green unit suite, successful HTTP response, or built image by itself proves production readiness. Actual destination file verification and host safety gates remain mandatory.
