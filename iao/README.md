# iao/ — Harvey na IA Operacional

Fork `ideias-online/harvey` do `ethanplusai/harvey` (MIT). Este diretório e as
mudanças abaixo são o que a IA Operacional acrescentou. Doc operacional (como a
Kate usa, onde está a config, como reverter): no cérebro,
`areas/produto/harvey.md`.

## O que mudou no código (diferença declarada contra o upstream)

| Arquivo | Mudança |
|---|---|
| `harvey/integrations/iao_gmail_sa.py` | provider novo `iao_gmail_sa`: envia pelo `email_iao.py` (repo `vendas`) com a SA gestao-iao + DWD, sem OAuth nem senha; lê respostas da caixa operacao@ (só leitura) |
| `harvey/integrations/mail_provider.py` | registra `iao_gmail_sa` como provider nativo |
| `harvey/agents/sender.py` | o orçamento do ciclo respeita `provider.remaining_today()` (limite compartilhado com o envio manual) |
| `harvey/config.py`, `harvey/brain.py` | `brain.language`: todo texto para gente sai no idioma configurado (pt-BR) |
| `harvey/brain.py` | skill `iao_rules` no Writer e no Handler (montado por `monta-regras.sh`) |
| `harvey/gate.py` | frases proibidas em português + saudação a "equipe" |
| `tests/test_iao_gmail_sa.py` | testes do provider e do gate PT |

## Arquivos daqui

- `importa_leads.py` — CSV das bases de prospecção do cérebro → `data/harvey.db`
- `rascunhos.py` — uma rodada de profile + Writer, sem envio, imprime a fila
- `monta-regras.sh` — monta `skills/iao_rules.md` (regras normativas do cérebro e do agentes-social-media)
- `systemd/` — `harvey-iao.service` (heartbeat) e `harvey-iao-dashboard.service` (só 127.0.0.1:5555)
- `requirements-iao.txt` — `cryptography` (o `email_iao.py` assina o JWT da SA)

## Instalar do zero

    git clone https://github.com/ideias-online/harvey ~/repos-iao/harvey && cd ~/repos-iao/harvey
    python3 -m venv .venv && .venv/bin/pip install -e . -r iao/requirements-iao.txt
    ln -s ~/brains/iaoperacional-brain/areas/produto/harvey/harvey.local.yaml harvey.local.yaml
    for f in product_knowledge competitive_intel; do
      ln -s ~/brains/iaoperacional-brain/areas/produto/harvey/treino-2026-10-20/$f.md skills/$f.md; done
    ln -sf ~/repos-iao/harvey/iao/systemd/harvey-iao*.service ~/.config/systemd/user/
    systemctl --user daemon-reload && systemctl --user enable --now harvey-iao harvey-iao-dashboard

## Reverter

    systemctl --user disable --now harvey-iao harvey-iao-dashboard
    rm ~/.config/systemd/user/harvey-iao*.service && systemctl --user daemon-reload

Nada mais no host depende do Harvey: o envio manual da Kate segue pelo `email_iao.py`.
