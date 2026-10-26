# Security reporting

## Scope

Report reproducible security concerns against the current default branch, including the commit SHA and affected component. Historical experiments and old snapshots are retained for reference; this document does not promise security support for any release or response time.

## Report privately when a channel is available

Open the repository's [Security tab](https://github.com/dev-belly/TradeForge/security) and check whether **Report a vulnerability** is available. If it is available, use that private reporting channel. This policy does not claim that private vulnerability reporting is enabled.

If no private reporting option is available, do not publish exploit steps, credentials, confidential data or a working exploit in a public issue or pull request. You may open a general [contact request](https://github.com/dev-belly/TradeForge/issues/new) asking for a private reporting channel, without including sensitive technical details. Wait for an agreed private channel before sharing them.

A useful private report contains the affected commit and environment, minimal reproduction, impact and any proposed mitigation. Use synthetic or public fixtures wherever possible; redact tokens and identifying records. Do not test against third-party services or accounts without authorization.

## Project-specific data handling

State whether the Python reference backend or compiled C++ backend is in use. For C++ changes, also follow the CMake, differential, and sanitizer checks in the CI workflow. Keep exchange credentials, private order flow, account IDs, positions and licensed feeds out of public reports.

Include the backend, seed, event order, instrument settings, fee/latency assumptions, and data capability flags. Prefer the saved public-depth fixture or a minimal synthetic sequence. Separate observed market events from modeled fills, fees and queue assumptions.

Security handling is best effort. Do not send passwords, access tokens, customer records or unredacted private datasets through any repository form.
