# Inert upstream GitHub configuration

This directory mirrors the complete `.github` tree inherited from the declared
`NixOS/nixpkgs` baseline. Every inherited workflow, composite action, bot
configuration, issue form, template, and document is preserved byte-for-byte,
but none remains at its GitHub-active path. In particular, GitHub executes
workflow files only directly under `.github/workflows`; the nested
`upstream-archive/workflows` path is inert.

The original path set, baseline commit, archive root, file mode, and SHA-256
digests are declared in `.github/axiomlayer/fleet-pin.json`. The active
integration workflow fails if a baseline file is omitted, restored to a live
path, changed in the archive, changed relative to the baseline commit, or if a
second executable workflow appears.
