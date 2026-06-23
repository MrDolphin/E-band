# Domain Docs

This repo uses a single-context domain documentation layout.

## Before exploring, read these

- `CONTEXT.md` at the repo root, if it exists.
- `docs/adr/`, if it exists.

If these files do not exist, proceed silently. Do not ask to create them unless the task is specifically about domain modeling or architecture documentation.

## Expected layout

```text
/
|-- CONTEXT.md
|-- docs/adr/
`-- Material/videotool_decompiled/remotevideo_usepdb/
```

## Use the glossary vocabulary

When output names a project/domain concept, prefer the terms defined in `CONTEXT.md`.

## Flag ADR conflicts

If an answer or implementation contradicts an existing ADR, surface that conflict explicitly.
