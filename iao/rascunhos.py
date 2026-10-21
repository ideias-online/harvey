#!/usr/bin/env python3
"""Uma rodada do Harvey so para rascunhos: le o site das empresas importadas
(profile, sem Claude) e roda o Writer ate nao sobrar prospect 'new' escrevivel.
Nada e enviado: com require_approval=true os rascunhos ficam em pending_review
no Outbox, esperando a Kate. Imprime os rascunhos ao final.

Uso: .venv/bin/python iao/rascunhos.py [--max-rodadas 3]
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harvey.agents.writer import Writer  # noqa: E402
from harvey.brain import Brain  # noqa: E402
from harvey.config import load_config, load_env  # noqa: E402
from harvey.pipeline import run_profile_stage  # noqa: E402
from harvey.state import StateManager  # noqa: E402


async def main(max_rodadas):
    config, env = load_config(), load_env()
    state = StateManager()
    await state.init_db()
    brain = Brain(state, config.brain)
    empresas, obs, _ = await run_profile_stage(state, limit=200)
    print(f"profile: {empresas} empresas lidas, {obs} observacoes")
    writer = Writer(brain, state, config, env)
    for i in range(max_rodadas):
        antes = await state.count_writable_prospects()
        if not antes:
            break
        await writer.run()
        depois = await state.count_writable_prospects()
        print(f"rodada {i + 1}: escreviveis {antes} -> {depois}")
        if depois >= antes:
            break
    itens = await state.get_outbox(status="pending_review", limit=500)
    print(f"\n{len(itens)} rascunho(s) em pending_review\n")
    for it in itens:
        print(f"=== para {it['to_email']} | passo {it['step']} | assunto: {it['subject']}")
        print(it["body"])
        print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-rodadas", type=int, default=3)
    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
    asyncio.run(main(ap.parse_args().max_rodadas))
