# Release notes — v1.4.0

_Released 2026-09-07_

**Audience:** Developers

## Summary

This release makes setup easier, configuration more predictable, and diagnostics safer while correcting checkout discovery.

## Added

- Added a guided project bootstrap that checks prerequisites before the first run. (commit: `1111111`)

## Changed

- Changed configuration discovery to prefer project-local settings before user defaults. (commit: `2222222`)

## Fixed

- Fixed repository detection when the checkout path contains spaces. (commit: `3333333`)

## Security

- Hardened rendered diagnostics so secrets and absolute local paths are not exposed. (commit: `4444444`)

[Compare the complete range](https://github.com/example/acme/compare/v1.3.0...v1.4.0)
