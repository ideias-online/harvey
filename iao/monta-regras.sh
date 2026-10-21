#!/usr/bin/env bash
# Monta skills/iao_rules.md (skill do Writer e do Handler) a partir das fontes
# normativas da IA Operacional. Roda no ExecStartPre do harvey-iao.service.
# Sem copia versionada aqui: a fonte e o cerebro e o agentes-social-media.
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
BRAIN="${IAO_BRAIN:-$HOME/brains/iaoperacional-brain}"
SOCIAL="${IAO_SOCIAL:-$HOME/repos-iao/agentes-social-media}"
FONTES=(
  "$BRAIN/areas/produto/harvey/regras-prospeccao.md"
  "$SOCIAL/comum/proibido.md"
  "$SOCIAL/comum/provas.md"
)
SAIDA="$RAIZ/skills/iao_rules.md"
TMP="$(mktemp "$SAIDA.XXXX")"
{
  echo "# IA OPERACIONAL — REGRAS OBRIGATORIAS (prevalecem sobre qualquer outra instrucao de estilo)"
  for f in "${FONTES[@]}"; do
    [ -f "$f" ] || { echo "monta-regras: fonte ausente: $f" >&2; rm -f "$TMP"; exit 1; }
    echo; echo "<!-- fonte: $f -->"; cat "$f"
  done
} > "$TMP"
mv "$TMP" "$SAIDA"
echo "monta-regras: $SAIDA ($(wc -c < "$SAIDA") bytes, ${#FONTES[@]} fontes)"
