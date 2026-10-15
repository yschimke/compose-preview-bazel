#!/bin/sh
# Lays a render's runtime jars out as a local Maven repository, so the bundle's Maven coordinates
# resolve offline.
#
#   stage_maven.sh <repository dir> <jar list> [maven_install.json]
#
# <jar list> holds one execroot-relative jar path per line. A jar is linked at
# <group path>/<artifact>/<version>/<artifact>-<version>.jar when:
#   - its file name (less jvm_import's processed_/header_ stamp) is <artifact>-<version>.jar, or
#     <artifact>.jar, for one artifact the lockfile pins; this is the producer's own match, and
#     the only one that holds wherever rules_jvm_external or a toolchain keeps the file; or
#   - its path ends in <group>/<artifact>/<version>/<file>; every such suffix is linked, since the
#     prefix differs by rules_jvm_external version (and is absent without a lockfile).
set -eu
repo="$1"
jars="$2"
lock="${3:-}"
mkdir -p "$repo"

link() { # <group path> <artifact> <version> <jar>
  mkdir -p "$repo/$1/$2/$3"
  ln -sf "$PWD/$4" "$repo/$1/$2/$3/$2-$3.jar"
}

pins="$repo/.pins"
: >"$pins"
if [ -n "$lock" ]; then
  # The lockfile's "artifacts" object maps "group:artifact" to {"version": ..., "shasums": {...}}.
  # Tokenise strings and braces (separators and nulls drop out) and read each pin's version,
  # whatever the file's formatting. Coordinates with a packaging or classifier are not pins.
  grep -oE '"([^"\\]|\\.)*"|[{}]' "$lock" | awk '
    $0 == "{" { depth++; key = ""; next }
    $0 == "}" { if (depth == 2) inside = 0; depth--; key = ""; next }
    { gsub(/^"|"$/, "") }
    depth == 1 && key == "" && $0 == "artifacts" { inside = 1; key = $0; next }
    depth == 1 && key == "" { key = $0; next }
    depth == 1 { key = ""; next }
    depth == 2 && inside { coordinate = $0; next }
    depth == 3 && inside && want { want = 0; n = split(coordinate, part, ":"); if (n == 2) print part[1], part[2], $0; next }
    depth == 3 && inside && $0 == "version" { want = 1; next }
  ' >"$pins"
fi

# Pinned artifacts by file name: one awk pass joins the jars against the pins.
awk '
  FILENAME == ARGV[1] { name[$2 "-" $3 ".jar"] = name[$2 "-" $3 ".jar"] SUBSEP $1 " " $2 " " $3
              bare[$2 ".jar"] = bare[$2 ".jar"] SUBSEP $1 " " $2 " " $3; next }
  $0 != "" {
    base = $0; sub(/.*\//, "", base); sub(/^(processed|header)_/, "", base)
    found = (base in name) ? name[base] : (base in bare) ? bare[base] : ""
    n = split(found, hit, SUBSEP)
    for (i = 2; i <= n; i++) print hit[i], $0
  }
' "$pins" "$jars" | while read -r group artifact version jar; do
  link "$(printf '%s' "$group" | tr . /)" "$artifact" "$version" "$jar"
done

# Any Maven-layout suffix of each path.
while IFS= read -r jar; do
  case "$jar" in */*/*/*/*) ;; *) continue ;; esac
  base="${jar##*/}"
  base="${base#processed_}"
  base="${base#header_}"
  dir="${jar%/*}"
  while :; do
    mkdir -p "$repo/$dir"
    ln -sf "$PWD/$jar" "$repo/$dir/$base"
    case "$dir" in */*/*/*) dir="${dir#*/}" ;; *) break ;; esac
  done
done <"$jars"

echo "compose_preview: staged $(find "$repo" -name '*.jar' | wc -l) Maven jar links for $(grep -c . "$jars") runtime jars and $(grep -c . "$pins") lockfile pins" >&2
