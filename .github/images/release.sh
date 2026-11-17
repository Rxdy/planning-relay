#!/usr/bin/env bash
# Publie une version : pose le tag vX.Y.Z sur main (ce qui est en production) et le pousse.
# La CI (promotion.yml) pose alors la version sur les images de la prod et crée la release GitHub avec le manifeste.
set -euo pipefail
V=${1:-}
[[ "$V" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Usage : make release V=1.2.3"; exit 1; }
git fetch -q --tags origin main
if git rev-parse -q --verify "refs/tags/v$V" >/dev/null; then
  echo "La version v$V existe déjà."
  exit 1
fi
commit=$(git rev-parse origin/main)
echo "Version v$V sur main : $(git log -1 --format='%h %s' "$commit")"
git tag -a "v$V" "$commit" -m "Version $V"
git push origin "v$V"
echo "Suivi : gh run watch \$(gh run list -w Promotion -L 1 --json databaseId -q '.[0].databaseId')"
