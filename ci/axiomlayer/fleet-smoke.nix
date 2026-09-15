{ source, system }:

let
  pkgs = import source {
    inherit system;
    config.allowUnfree = false;
  };
in
pkgs.runCommand "axiomlayer-nixpkgs-smoke-${system}"
{
  nativeBuildInputs = [
    pkgs.hello
    pkgs.ripgrep
    pkgs.sqlite
  ];
}
  ''
    set -eu

    test "$(hello)" = "Hello, world!"

    printf 'fleet integration\n' > "$TMPDIR/corpus.txt"
    test "$(rg --only-matching 'fleet integration' "$TMPDIR/corpus.txt")" = "fleet integration"

    sqlite_result="$(${pkgs.sqlite}/bin/sqlite3 "$TMPDIR/fleet.db" \
      "CREATE VIRTUAL TABLE docs USING fts5(body); INSERT INTO docs VALUES ('fleet integration'); SELECT body FROM docs WHERE docs MATCH 'fleet';")"
    test "$sqlite_result" = "fleet integration"

    mkdir -p "$out"
    {
      hello --version | head -n 1
      rg --version | head -n 1
      ${pkgs.sqlite}/bin/sqlite3 --version
      printf 'fts5=verified\n'
    } > "$out/versions.txt"
  ''
