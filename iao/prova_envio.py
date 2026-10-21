#!/usr/bin/env python3
"""Prova ponta a ponta do provider iao_gmail_sa (t_b84784f5), pelo caminho real
do Harvey: prospect de teste = operacao@iaoperacional.com.br, item APROVADO no
Outbox, Sender._drain_due() envia (email_iao.py, kate@, prospeccao), depois
operacao@ responde na mesma conversa e Handler._run_native() le e classifica.

Fases (rodar em ordem):  enviar | responder | ler
"""

import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harvey.agents.handler import Handler  # noqa: E402
from harvey.agents.sender import Sender  # noqa: E402
from harvey.brain import Brain  # noqa: E402
from harvey.config import load_config, load_env  # noqa: E402
from harvey.models.company import Company  # noqa: E402
from harvey.models.prospect import Prospect  # noqa: E402
from harvey.state import StateManager  # noqa: E402

TESTE = "operacao@iaoperacional.com.br"
ESTADO = Path("data/prova-envio.json")


async def enviar():
    config, env = load_config(), load_env()
    state = StateManager()
    await state.init_db()
    cid = await state.add_company(Company(name="IA Operacional (teste)", domain="iaoperacional.com.br",
                                          website="https://iaoperacional.com.br", source="iao:prova"))
    p = await state.get_prospect_by_email(TESTE)
    if not p:
        pid = await state.add_prospect(Prospect(
            company_id=cid, first_name="Operacao", last_name="Teste", email=TESTE,
            email_status="verified", email_verified=True, title="Caixa de teste",
            source="iao:prova", company="IA Operacional (teste)"))
        p = await state.get_prospect(pid)
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    item = await state.add_outbox_item(
        prospect_id=p.id, campaign_id="prova-t_b84784f5", step=1, to_email=TESTE,
        subject="teste harvey t_b84784f5",
        body=("Operação,\n\nEste é o envio de prova do Harvey pelo provider iao_gmail_sa "
              "(Gmail API pela SA, remetente kate@, assinatura do email_iao.py).\n\n"
              "Pode responder este e-mail para provar a leitura da resposta?"),
        send_at=now, status="approved", provider="iao_gmail_sa")
    sender = Sender(Brain(state, config.brain), state, config, env)
    sender.send_pacing = False
    await sender._drain_due()
    row = await state.get_outbox_item(item)
    print(json.dumps({k: row.get(k) for k in ("id", "status", "to_email", "message_id", "thread_ref",
                                               "sent_at", "error")}, ensure_ascii=False))
    ESTADO.write_text(json.dumps({"item": item, "message_id": row.get("message_id"),
                                  "thread_ref": row.get("thread_ref")}))


def responder():
    import importlib
    sys.path.insert(0, str(Path("~/repos-iao/vendas/scripts").expanduser()))
    e = importlib.import_module("email_iao")
    st = json.loads(ESTADO.read_text())
    msg = EmailMessage()
    msg["From"] = f"Operacao IA Operacional <{TESTE}>"
    msg["To"] = "kate@iaoperacional.com.br"
    msg["Subject"] = "Re: teste harvey t_b84784f5"
    msg["Message-ID"] = make_msgid(domain="iaoperacional.com.br")
    msg["In-Reply-To"] = st["message_id"]
    msg["References"] = st["message_id"]
    msg.set_content("Recebido. Tenho interesse, podemos falar quinta às 10h?\n\nOperação")
    code, d = e._http("POST", f"{e.GMAIL}/messages/send", e.token([e.S_SEND]),
                      {"raw": e._b64u(msg.as_bytes()), "threadId": st["thread_ref"]})
    print("resposta enviada:", code, d.get("id"), d.get("threadId"), d.get("labelIds"))
    time.sleep(8)
    code, m = e._http("GET", f"{e.GMAIL}/messages/{d['id']}?format=metadata", e.token([e.S_READ]))
    print("labels da resposta na caixa:", m.get("labelIds"))


async def ler():
    config, env = load_config(), load_env()
    state = StateManager()
    handler = Handler(Brain(state, config.brain), state, config, env)
    ok = await handler._run_native()
    p = await state.get_prospect_by_email(TESTE)
    print("handler completo:", ok, "| status do prospect:", p.status)
    for stt in ("open", "replied", "meeting_booked", "closed"):
        for c in await state.get_conversations_by_status(stt):
            if c.prospect_id == p.id:
                print("conversa:", stt, "| intent:", c.intent, "| stage:", c.stage,
                      "| mensagens:", [m.sender for m in c.thread])
    for it in await state.get_outbox(limit=500):
        if it["to_email"] == TESTE:
            print("outbox:", it["kind"], it["status"], it["subject"], "|", (it["body"] or "")[:300].replace("\n", " "))


if __name__ == "__main__":
    fase = sys.argv[1]
    if fase == "enviar":
        asyncio.run(enviar())
    elif fase == "responder":
        responder()
    else:
        asyncio.run(ler())
