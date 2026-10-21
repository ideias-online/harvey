#!/usr/bin/env bash
# Importa as bases listadas em areas/produto/harvey/bases.txt (uma por linha,
# caminho relativo a areas/comercial/prospeccao/, # comenta) para o Harvey.
# Roda pelo harvey-iao-importa.timer. Idempotente; arquivo ausente e pulado.
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
BRAIN="${IAO_BRAIN:-$HOME/brains/iaoperacional-brain}"
LISTA="$BRAIN/areas/produto/harvey/bases.txt"
PROSP="$BRAIN/areas/comercial/prospeccao"
mapfile -t ARQS < <(sed -e 's/#.*//' -e 's/[[:space:]]*$//' "$LISTA" | grep -v '^$' | sed "s|^|$PROSP/|")
[ "${#ARQS[@]}" -gt 0 ] || { echo "importa: nenhuma base em $LISTA"; exit 0; }
cd "$RAIZ"
exec .venv/bin/python iao/importa_leads.py "${ARQS[@]}"
