#!/usr/bin/env python3
"""Dry-run do provider iao_gmail_sa sobre a fila do Outbox: para cada rascunho,
roda o gate deterministico do Harvey e monta o MIME real pelo email_iao.py
(--seco), SEM enviar e SEM mudar o status do item. Grava os MIMEs em --saida.

Uso: .venv/bin/python iao/dry_run.py [--n 10] [--saida DIR] [--status pending_review]
"""

import argparse
import asyncio
import email
import email.policy
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["HARVEY_IAO_DRY_RUN"] = "1"

from harvey.config import load_config, load_env  # noqa: E402
from harvey.gate import pre_send_check  # noqa: E402
from harvey.integrations.mail_provider import get_mail_provider  # noqa: E402
from harvey.state import StateManager  # noqa: E402


async def main(n, saida, status):
    config = load_config()
    provider = get_mail_provider(config, load_env())
    assert provider is not None and provider.dry_run, "provider sem dry-run"
    state = StateManager()
    itens = (await state.get_outbox(status=status, limit=500))[:n]
    saida.mkdir(parents=True, exist_ok=True)
    allow_risky = config.channels.email.send_to_risky
    print(f"provider={provider.name} remetente={provider.sender_address} dry_run={provider.dry_run} "
          f"restante_hoje={provider.remaining_today()}")
    for it in itens:
        p = await state.get_prospect(it["prospect_id"])
        gate = pre_send_check(it["to_email"], it["subject"], it["body"], prospect=p,
                              allow_risky=allow_risky, kind=it["kind"])
        r = await provider.send_email(it["to_email"], it["subject"], it["body"])
        mime = provider.last_mime
        arq = saida / f"{it['to_email'].replace('@', '_at_')}.eml"
        arq.write_text(mime, encoding="utf-8")
        m = email.message_from_string(mime, policy=email.policy.default)
        texto = m.get_body(preferencelist=("plain",)).get_content()
        print(f"- {it['to_email']:42} status_email={p.email_status:8} "
              f"gate={'OK' if gate else 'BLOQUEIA: ' + '; '.join(gate.reasons)} | "
              f"From={m['From']} | List-Unsubscribe={'sim' if m['List-Unsubscribe'] else 'nao'} | "
              f"assinatura={'sim' if 'WhatsApp: (11) 5104-4434' in texto else 'NAO'} | "
              f"html={'sim' if m.get_body(preferencelist=('html',)) else 'nao'} | "
              f"enviado={'NAO' if not r.ok else 'SIM?!'} ({r.error}) | {arq.name}")
    print(f"\n{len(itens)} item(ns); status no Outbox inalterado; envios.csv inalterado.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--saida", type=Path, default=Path("data/dry-run"))
    ap.add_argument("--status", default="pending_review")
    a = ap.parse_args()
    asyncio.run(main(a.n, a.saida, a.status))
