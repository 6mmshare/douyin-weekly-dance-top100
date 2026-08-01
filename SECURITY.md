# Security and privacy

## Do not publish browser profiles

`data/profiles/` may contain cookies, local storage, IndexedDB data and other authenticated browser state. Treat it like a password.

Before publishing a fork or attaching a bug report, remove:

- `data/`
- `output/`
- `logs/`
- `debug_screenshots/`
- any exported HTML, Excel, CSV or JSON files containing captured account/video metadata

## Reporting a vulnerability

Open a GitHub issue without including cookies, tokens, screenshots of private pages, account identifiers or raw profile directories. Describe the affected version and reproducible steps using a clean test profile.
