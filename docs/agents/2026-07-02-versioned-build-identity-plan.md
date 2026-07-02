# Versioned Build Identity Implementation Plan

## Task 1: Build identity behavior

- Add a failing self-test for embedded commit selection.
- Update `AppBuildInfo` to prefer immutable assembly metadata and remove runtime Git lookup.
- Generate `BuildCommit` assembly metadata from an MSBuild property.

## Task 2: Unified publisher

- Add `publish_versioned_remotevideo.ps1`.
- Derive the commit, directory, and assembly name from one value.
- Build and validate all three identities.
- Update `run_remotevideo.cmd` after successful validation.

## Task 3: Verification and delivery

- Run all modem self-tests.
- Publish a versioned build.
- Verify assembly metadata and output names.
- Commit and push only the intended source files.
