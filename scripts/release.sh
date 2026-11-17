#!/usr/bin/env bash
# Publier une version : scripts/release.sh 1.4.0
# Pose le tag v1.4.0 sur main (ce qui est en production) et le pousse. Rien n'est reconstruit : la CI (promotion.yml)
# pose la version sur les images de la prod, construites à la fusion dans dev, et crée la Release GitHub avec le manifeste.
# Pour figer une version sur le Pi : mettre l'image en :v1.4.0 au lieu de :latest dans son compose.
set -euo pipefail
V="${1:-}"
[[ "$V" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Usage : scripts/release.sh X.Y.Z (ex. 1.4.0)"; exit 1; }
git fetch -q origin main --tags
[ "$(git branch --show-current)" = main ] || { echo "Une version se publie depuis main."; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "Des changements ne sont pas commités."; exit 1; }
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || { echo "main n'est pas à jour avec GitHub (git pull, ou git push)."; exit 1; }
if git rev-parse -q --verify "refs/tags/v$V" >/dev/null; then echo "La version v$V existe déjà."; exit 1; fi
last="$(git tag -l 'v*' --sort=-v:refname | head -1)"
echo "Dernière version : ${last:-aucune}  →  v$V"
git tag -a "v$V" -m "Version $V"
git push origin "v$V"
echo "Tag v$V poussé. Suivre la promotion : gh run watch"
