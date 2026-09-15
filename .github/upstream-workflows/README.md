# Inert upstream workflows

These files are byte-for-byte copies of the workflows inherited from
`NixOS/nixpkgs`. GitHub only executes workflow files directly under
`.github/workflows`, so this directory preserves upstream history without
importing NixOS bot, merge, review, cache-write, release, deployment, or
publisher authority into AxiomLayer.

The expected file set and SHA-256 digests are declared in
`.github/axiomlayer/fleet-pin.json`. The active integration workflow fails if an
archived file changes, an external Action loses its full-SHA pin, or another
executable workflow appears.
