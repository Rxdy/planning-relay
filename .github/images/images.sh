#!/usr/bin/env bash
# Images Docker du projet : chaque service est construit UNE fois, à la fusion dans dev, puis la même image est promue.
#
#   Empreinte  chaque service a une empreinte calculée sur ses seules sources (services.json) ; son image porte le tag
#              c-<empreinte>. Si cette image existe déjà, le service n'est pas reconstruit : seul ce qui a changé l'est.
#   Manifeste  pour un commit, l'image et le digest exacts de chaque service. Il est publié sur GHCR
#              (<projet>-manifeste:<commit>, :dev, :staging, :prod, :vX.Y.Z) et joint à la release GitHub.
#   Cycle      fusion dans dev → build des services changés, tag dev ; dev → staging : tag staging ;
#              staging → main : tags prod et latest ; tag vX.Y.Z sur main (make release) : tag de version et release.
#   Durée      une image qui ne porte ni dev, staging, prod, latest ni une version est supprimée après 14 jours.
#
# Commandes (utilisées par la CI) :
#   images.sh plan <commit>                  services à construire (tableau JSON sur une ligne)
#   images.sh manifeste <commit> <fichier>   écrit le manifeste du commit ; échoue si l'image d'un service manque
#   images.sh etiqueter <fichier> <tag>...   pose les tags sur les images du manifeste, puis publie le manifeste
#   images.sh nettoyer <jours> <essai>       supprime les images non promues plus vieilles que <jours> (essai=true : liste)
set -euo pipefail

ICI=$(dirname "$0")
CONF=$ICI/services.json
RECETTE=1 # changer ce numéro force la reconstruction de toutes les images (ex. mise à jour de l'image de base)
PROPRIO=$(printf '%s' "${GITHUB_REPOSITORY_OWNER:?}" | tr '[:upper:]' '[:lower:]')
REG=ghcr.io/$PROPRIO
PROJET=$(jq -r .projet "$CONF")
MANIFESTES=$REG/$PROJET-manifeste
PROTEGES='^(dev|staging|prod|latest|v[0-9]+\.[0-9]+\.[0-9]+)$'

services() { jq -r '.services | keys[]' "$CONF"; }
image() { printf '%s/%s' "$REG" "$(jq -r --arg s "$1" '.services[$s].image' "$CONF")"; }
digest() { docker buildx imagetools inspect "$1" --format '{{json .Manifest}}' 2>/dev/null | jq -r .digest 2>/dev/null || true; }

# Empreinte d'un service : sa configuration et l'empreinte git de chacune de ses sources au commit donné.
empreinte() {
  local s=$1 commit=$2 p
  {
    echo "recette $RECETTE"
    jq -c --arg s "$s" '{plateformes, service: .services[$s]}' "$CONF"
    jq -r --arg s "$s" '.services[$s].sources[]' "$CONF" | while read -r p; do
      echo "$p $(git rev-parse -q --verify "$commit:$p" || echo absent)"
    done
  } | sha256sum | cut -c1-16
}

plan() {
  local commit=$1 s e img out='[]'
  for s in $(services); do
    e=c-$(empreinte "$s" "$commit")
    img=$(image "$s")
    if [ -n "$(digest "$img:$e")" ]; then
      echo "$s : inchangé, image $img:$e réutilisée" >&2
      continue
    fi
    echo "$s : à construire ($img:$e)" >&2
    out=$(jq -c --arg s "$s" --arg img "$img" --arg e "$e" --arg commit "$commit" --slurpfile conf "$CONF" '
      ($conf[0].services[$s]) as $svc
      | . + [{
          service: $s, image: $img, empreinte: $e,
          contexte: ($svc.contexte // "."), dockerfile: ($svc.dockerfile // ""), cible: ($svc.cible // ""),
          build_args: (($svc.build_args // []) | map(gsub("\\{commit\\}"; $commit) | gsub("\\{court\\}"; $commit[0:7])) | join("\n"))
        }]' <<<"$out")
  done
  echo "$out"
}

manifeste() {
  local commit=$1 fichier=$2 s e img d manque=0 svcs='{}'
  for s in $(services); do
    e=c-$(empreinte "$s" "$commit")
    img=$(image "$s")
    d=$(digest "$img:$e")
    if [ -z "$d" ]; then
      echo "::error::$s : aucune image $img:$e. Ce contenu n'a jamais été construit : il doit d'abord passer par une fusion dans dev."
      manque=1
      continue
    fi
    svcs=$(jq -c --arg s "$s" --arg img "$img" --arg e "$e" --arg d "$d" \
      '. + {($s): {image: $img, empreinte: $e, digest: $d, ref: ($img + "@" + $d)}}' <<<"$svcs")
  done
  [ "$manque" = 0 ] || return 1
  # pas de date : pour un même commit, le manifeste est identique d'une étape à l'autre (il est re-étiqueté, pas republié)
  jq -n --arg p "$PROJET" --arg r "${GITHUB_REPOSITORY:-}" --arg c "$commit" --argjson s "$svcs" \
    '{projet: $p, depot: $r, commit: $c, services: $s}' >"$fichier"
  cat "$fichier"
}

etiqueter() {
  local fichier=$1 s ref img t commit
  shift
  for s in $(jq -r '.services | keys[]' "$fichier"); do
    ref=$(jq -r --arg s "$s" '.services[$s].ref' "$fichier")
    img=$(jq -r --arg s "$s" '.services[$s].image' "$fichier")
    local args=()
    for t in "$@"; do args+=(-t "$img:$t"); done
    docker buildx imagetools create "${args[@]}" "$ref"
    echo "$s : $ref → $*"
  done
  commit=$(jq -r .commit "$fichier")
  # manifeste déjà publié pour ce commit, au même contenu : on lui ajoute les tags
  local deja
  deja=$(mktemp -d)
  if oras pull -o "$deja" "$MANIFESTES:$commit" >/dev/null 2>&1 &&
    [ "$(jq -S . "$deja/$(basename "$fichier")" 2>/dev/null)" = "$(jq -S . "$fichier")" ]; then
    oras tag "$MANIFESTES:$commit" "$@"
    return
  fi
  (
    cd "$(dirname "$fichier")"
    oras push "$MANIFESTES:$(IFS=,; echo "$commit,$*")" \
      --artifact-type application/vnd.rxdy.manifeste.v1+json \
      --annotation "org.opencontainers.image.source=https://github.com/${GITHUB_REPOSITORY:?}" \
      "$(basename "$fichier"):application/json"
  )
}

nettoyer() {
  local jours=$1 essai=$2 limite pkg enc versions garder enfants d v
  limite=$(date -u -d "-$jours days" +%FT%TZ)
  echo "Suppression des versions sans tag protégé et non modifiées depuis $limite (essai : $essai)"
  for pkg in $(jq -r '.services[].image' "$CONF") "$PROJET-manifeste"; do
    enc=${pkg//\//%2F}
    if ! versions=$(gh api --paginate "/users/$PROPRIO/packages/container/$enc/versions" \
      --jq '.[] | {id, digest: .name, tags: .metadata.container.tags, maj: .updated_at}' | jq -s .); then
      echo "$pkg : paquet introuvable, ignoré"
      continue
    fi
    # gardées : un tag protégé (étape ou version), ou modifiées récemment
    garder=$(jq -c --arg l "$limite" --arg p "$PROTEGES" '[.[] | select((.tags | any(test($p))) or .maj > $l)]' <<<"$versions")
    # une image multi-architecture (ou avec attestation) référence des manifestes sans tag : ceux des images gardées restent
    enfants=$(for d in $(jq -r '.[] | select(.tags | length > 0) | .digest' <<<"$garder"); do
      docker buildx imagetools inspect --raw "$REG/$pkg@$d" | jq -r '.manifests[]?.digest'
    done | jq -Rsc 'split("\n") | map(select(. != ""))')
    jq -c --argjson g "$garder" --argjson e "$enfants" \
      '.[] | select((.id as $i | $g | map(.id) | index($i) | not) and (.digest as $d | $e | index($d) | not))' <<<"$versions" |
      while read -r v; do
        echo "$pkg : suppression de $(jq -r '.digest + " [" + (.tags | join(",")) + "] " + .maj' <<<"$v")"
        [ "$essai" = true ] || gh api -X DELETE "/users/$PROPRIO/packages/container/$enc/versions/$(jq -r .id <<<"$v")" >/dev/null
      done
  done
}

case "${1:-}" in
  plan | manifeste | etiqueter | nettoyer)
    cmd=$1
    shift
    "$cmd" "$@"
    ;;
  *)
    sed -n '2,20p' "$0"
    exit 1
    ;;
esac
