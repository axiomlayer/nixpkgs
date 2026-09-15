# AxiomLayer Nixpkgs integration

This fork is an integration gate, not a second Nixpkgs distribution or release
authority. The fleet snapshot is `nixos-26.05` at
`c3eea5b2156db11c7eeeada3dc737711255b253e`, exactly as declared by
`AxiomLayer/dotfiles` pull request 49.

The only active workflow has read-only repository permissions. It does not use
environments, secrets, OIDC, cache signing keys, artifact uploads, package
writes, release APIs, deployment credentials, or publisher identities. Every
external action is pinned to a full commit SHA.

The seventeen workflows inherited from `NixOS/nixpkgs` are retained
byte-for-byte under `.github/upstream-workflows`. GitHub does not execute files
from that directory, so upstream bot, merge, review, cache-write, and maintainer
automation cannot acquire authority in the AxiomLayer namespace. The policy
verifier checks both the archived bytes and the single-active-workflow boundary.

## What the gate proves

For the immutable fleet pin, native hosted runners import the package set on
Linux x86_64, Linux ARM64, macOS Intel, and macOS ARM64. They evaluate the fleet
runtime package attributes, evaluate a minimal NixOS closure on both Linux
systems, and build a deliberately small smoke derivation containing GNU Hello,
ripgrep, and SQLite. The derivation performs a real SQLite FTS5 query.

Package evaluation here proves that the Nix integration layer remains usable;
it does not make Nixpkgs the source authority for fleet runtimes or require its
package versions to equal the separately pinned Deno, Node, Go, Python, and
other runtime forks.

Windows is intentionally not represented as a native Nix system. Ocelot and
Siberian consume `aarch64-linux` and `x86_64-linux`, respectively, through WSL;
the target mapping is explicit in `fleet-pin.json` and checked against the
Dotfiles acceptance target set.

On a weekly schedule or explicit dispatch, the workflow resolves the upstream
`nixos-26.05` branch once to a full commit SHA and runs the same matrix against
that immutable candidate. This is an early warning only: it cannot update the
fork, rewrite a pin, publish an artifact, or promote a release.

## Local contract check

Run the static contract suite with clean checkouts of this harness, the exact
Nixpkgs snapshot, and the exact Dotfiles control commit:

```console
ci/axiomlayer/test-contracts.sh HARNESS SNAPSHOT DOTFILES
```

Native Nix build/evaluation remains CI and hardware acceptance work when Nix is
not installed on the authoring host.
