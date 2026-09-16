# AutoRename agent guidance

## Ownership
- This repository owns generic document OCR verification/execution, canonical naming, classification, route-readiness state, collision safety, audit/cache behavior, and generic routing mechanics.
- Do not commit personal cloud paths, account identifiers, scheduling policy, or private destination mappings here. Supply those through external routing configuration.

## Safety
- Preview is the default for new processing and routing workflows; mutations require an explicit apply action.
- Do not infer processing completion from a filename alone. Route readiness requires verified searchable text, a canonical filename, sufficient classification confidence, and no review condition.
- Do not resolve same-name files by blindly adding numeric suffixes in the normalization/routing path. Hash candidates; exact duplicates and different-content collisions remain non-destructive review outcomes.
- Do not automatically delete duplicate source files.

## Command surface
- `autorename` is the canonical command family. `autorename-pdf` is a compatibility name during migration.
- Generic reusable routes are `process` and `route`; `organize` remains the Downloads-specific compatibility workflow.
- Keep `commands.toml` synchronized with PATH-visible command changes.

## Verification
- Use synthetic public fixtures only.
- Run the focused normalization/pipeline tests and the existing repository test suite after processing changes.
