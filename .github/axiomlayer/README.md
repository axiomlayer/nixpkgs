# AxiomLayer Nixpkgs integration

This fork is an integration gate, not a second Nixpkgs distribution or release
authority. The fleet snapshot is `nixos-26.05` at
`c3eea5b2156db11c7eeeada3dc737711255b253e`, exactly as declared by
`axiomlayer/dotfiles` pull request 49.

The only active workflow has read-only repository permissions. It does not use
environments, secrets, OIDC, cache signing keys, artifact uploads, package
writes, release APIs, deployment credentials, publisher identities, or access
to the private Dotfiles control repository. Every external action is pinned to
a full commit SHA, and every checkout disables credential persistence.

Hosted CI installs Nix 2.35.2 through a fleet-standard, content-addressed
wrapper. The manifest and an independent verifier bind the release launcher,
the wrapper itself, both Nix expressions, and all four native Nix tarballs.
Before executing the verified launcher, the wrapper replaces the process
environment with a small allowlist that contains no GitHub, Actions, Cachix,
proxy, or repository credentials; even the initial download ignores user curl
configuration and proxy variables. The later evaluator and builder export the
exact Git commit, content-address both source and harness through the Nix
store, and run with pure and restricted evaluation under a fixed environment
allowlist. Upstream Nix expressions therefore cannot inherit GitHub runtime
tokens, credential variables, ambient Nix configuration, or arbitrary mutable
host paths. No delegated Nix bootstrap action executes in the AxiomLayer
workflow.

The complete 36-file `.github` baseline inherited from `NixOS/nixpkgs` is
retained byte-for-byte under `.github/upstream-archive`, including all 17
workflow files plus the upstream composite action, Dependabot, labeler,
Zizmor, issue-form, pull-request-template, and bot documentation files. Their
original paths, archive SHA-256 digests, file mode, baseline commit, and exact
baseline `.github` tree are bound in the manifest and immutable verifier
constants. The sole active workflow is independently SHA-256 bound as well.
GitHub does not execute the quarantined paths, so
upstream bot, merge, review, cache-write, release, and maintainer automation
cannot acquire authority in the AxiomLayer namespace. The policy verifier
compares every archive byte to the declared Git baseline, rejects any symlink
in the control surfaces, and enforces the single-active-workflow boundary.

Every job in the active workflow independently refuses any repository other
than exact lowercase `axiomlayer/nixpkgs`. Same-repository pull requests run
only at their event merge SHA and merge ref on dated GitHub-hosted runners.
Pushes bind the event SHA to the protected branch's `after` commit. Schedule
and manual runs require the protected `master` ref and its exact workflow
identity. Every harness checkout explicitly names the validated event SHA.
No path in this public fork selects a fleet self-hosted runner. The only
required status is the unconditional, Action-free `Required Nixpkgs integration
authority` job. It fails unless the immutable fleet matrix succeeded and the
upstream candidate matrix either succeeded on schedule/manual runs or was
correctly skipped on same-repository pull requests and protected-main pushes.

## What the gate proves

For the immutable fleet pin, native hosted runners import the package set on
Linux x86_64, Linux ARM64, macOS Intel, and macOS ARM64. They evaluate the fleet
runtime package attributes, evaluate a minimal NixOS closure on both Linux
systems, and build a deliberately small smoke derivation containing GNU Hello,
ripgrep, and SQLite. The exact runner-to-system mapping is immutable, not only
the set of four names. The derivation performs a real SQLite FTS5 query.

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

Public fork CI checks the minimized control binding and exact Nixpkgs snapshot
without trying to cross the private repository boundary:

```console
ci/axiomlayer/test-contracts.sh HARNESS SNAPSHOT
```

Before publication, run the stronger authoring check with clean checkouts of
this harness, the exact Nixpkgs snapshot, and the exact Dotfiles control commit:

```console
ci/axiomlayer/test-contracts.sh HARNESS SNAPSHOT DOTFILES
```

Native Nix build/evaluation remains CI and hardware acceptance work when Nix is
not installed on the authoring host. The local contract suite statically proves
the bootstrap bytes and exercises digest, environment, credential, and
delegated-bootstrap tamper refusals without installing Nix.
