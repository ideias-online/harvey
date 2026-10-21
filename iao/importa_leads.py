#!/usr/bin/env python3
"""Importa leads das bases de prospeccao da IA Operacional para o Harvey.

Le os CSVs do cerebro (areas/comercial/prospeccao/*.csv, formato da onda1-v2 e
do leads-2026-10-21) e grava empresa + decisor no data/harvey.db. Idempotente:
dominio e email sao chaves; rodar de novo nao duplica (so atualiza o status do
email quando o teste mudou).

Status do email (coluna teste_resultado / resultado_teste / status_email):
  aceito | valido | ok          -> verified  (sai, se a Kate aprovar)
  catch-all | indeterminado     -> risky     (o Writer escreve; o gate SEGURA o
                                              envio enquanto send_to_risky=false)
  recusado | invalido | bounce  -> invalid   (nem escreve)
  vazio / outro                 -> guess     (nem escreve)
Regra da casa: "indeterminado nao conta como testado" (prospeccao/README.md).

Uso:
  .venv/bin/python iao/importa_leads.py ARQ.csv [ARQ2.csv ...] [--seco]
"""

import argparse
import asyncio
import csv
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harvey.models.company import Company  # noqa: E402
from harvey.models.prospect import Prospect  # noqa: E402
from harvey.state import StateManager  # noqa: E402

STATUS = {
    "aceito": "verified", "valido": "verified", "válido": "verified", "ok": "verified",
    "verified": "verified",
    "catch-all": "risky", "catchall": "risky", "indeterminado": "risky", "risky": "risky",
    "recusado": "invalid", "invalido": "invalid", "inválido": "invalid", "bounce": "invalid",
    "invalid": "invalid",
}
# Atualizacao so sobe de nivel: um CSV antigo sem teste nao rebaixa um email aceito;
# um teste que recusou (invalid) sempre prevalece.
RANK = {"guess": 0, "": 0, "risky": 1, "verified": 2, "invalid": 3}


def col(row, *names):
    for n in names:
        v = (row.get(n) or "").strip()
        if v:
            return v
    return ""


def email_status(row):
    bruto = col(row, "teste_resultado", "status_email", "email_status", "resultado_teste").lower()
    for chave, valor in STATUS.items():
        if bruto.startswith(chave):
            return valor
    # leads-2026-10-21 (t_cbc4ac43): sem teste de caixa, so MX conferido. Entra como
    # risky para o Writer rascunhar; o gate segura o envio ate chegar teste aceito.
    if col(row, "mx_ok").lower() in ("1", "sim", "s", "true", "ok", "yes"):
        return "risky"
    return "guess"


def nome(decisor):
    limpo = re.sub(r"\(.*?\)", "", decisor or "").strip()
    partes = limpo.split()
    if not partes:
        return "", ""
    return partes[0].title(), " ".join(partes[1:]).title()


FREE_MAIL = {"gmail.com", "googlemail.com", "hotmail.com", "outlook.com", "live.com", "yahoo.com",
             "yahoo.com.br", "uol.com.br", "bol.com.br", "terra.com.br", "icloud.com", "creci.org.br"}


def dominio_empresa(row, email):
    """Dominio da empresa: o do email, salvo webmail/creci; ai o do site."""
    d = email.split("@", 1)[1]
    if d not in FREE_MAIL:
        return d
    for c in ("site", "website", "carteira_fonte_url", "vaga_url"):
        u = col(row, c)
        if u.startswith("http"):
            host = urlparse(u).netloc.lower()
            host = host[4:] if host.startswith("www.") else host
            if host and host not in FREE_MAIL:
                return host
    return ""


def site(row, dominio):
    for c in ("site", "website", "carteira_fonte_url", "vaga_url"):
        u = col(row, c)
        if u.startswith("http"):
            p = urlparse(u)
            if dominio and dominio in p.netloc:
                return f"{p.scheme}://{p.netloc}"
    return f"https://{dominio}" if dominio else ""


def segmento(row):
    s = col(row, "segmento", "industria", "industry")
    if s:
        return s
    if col(row, "carteira_locacao") or col(row, "lote").upper() == "A":
        return "Imobiliária (locação)"
    if col(row, "vaga", "vaga_titulo", "vaga_cargo") or "vaga" in col(row, "dor").lower():
        return "Empresa com vaga no financeiro"
    return ""


def sinais(row):
    out = []
    cart = col(row, "carteira_locacao")
    if cart:
        url = col(row, "carteira_fonte_url")
        out.append({"type": "rental_portfolio",
                    "detail": f"carteira de locação com {cart} imóveis anunciados" + (f" ({url})" if url else "")})
    vaga = col(row, "vaga", "vaga_titulo", "vaga_cargo")
    if vaga:
        url = col(row, "vaga_url")
        out.append({"type": "HIRING_ROLE",
                    "detail": f"vaga aberta: {vaga}" + (f" ({url})" if url else "")})
    dor = col(row, "dor")
    if dor and not out:
        url = col(row, "dor_fonte_url")
        tipo = "HIRING_ROLE" if "vaga" in dor.lower() else "rental_portfolio"
        out.append({"type": tipo, "detail": dor + (f" ({url})" if url else "")})
    return out


ENVIOS = Path("~/brains/iaoperacional-brain/areas/comercial/prospeccao/envios.csv").expanduser()
LEDGER = Path(__file__).resolve().parents[1] / "data" / "iao_enviados.txt"


def contatados_fora(envios):
    """Emails que ja receberam prospeccao fora do Harvey (envios.csv, Message-ID fora do
    ledger do Harvey). Esses nao recebem cold email do Harvey."""
    try:
        ours = {l.strip() for l in LEDGER.read_text().splitlines() if l.strip()}
    except OSError:
        ours = set()
    try:
        with Path(envios).open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return set()
    out = set()
    for r in rows:
        if (r.get("message_id") or "") in ours:
            continue
        for e in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", r.get("para") or ""):
            out.add(e.lower())
    return out


async def importa(arquivos, seco=False, envios=ENVIOS):
    state = StateManager()
    await state.init_db()
    manuais = contatados_fora(envios)
    novos = atualizados = pulados = ja_contatados = 0
    for arq in arquivos:
        if not Path(arq).is_file():
            print(f"  {arq}: ainda nao existe, pulado")
            continue
        with open(arq, encoding="utf-8-sig", newline="") as f:
            linhas = list(csv.DictReader(f))
        for row in linhas:
            email = col(row, "email").lower()
            empresa = col(row, "empresa", "company")
            if "@" not in email or not empresa:
                pulados += 1
                continue
            dominio = dominio_empresa(row, email)
            first, last = nome(col(row, "decisor", "decisor_nome", "nome", "contato"))
            if not first:
                pulados += 1
                continue
            st = email_status(row)
            notas = []
            if col(row, "saudacao"):
                notas.append(f"Saudação aprovada: \"{col(row, 'saudacao')}\"")
            for s in sinais(row):
                notas.append(s["detail"])
            cidade = col(row, "cidade", "cidade_uf")
            if cidade:
                notas.append(f"Cidade: {cidade}")
            if seco:
                print(f"{empresa:40} {email:40} {st:9} {first} {last}")
                continue
            comp = Company(name=empresa, domain=dominio, website=site(row, dominio),
                           industry=segmento(row), location=cidade,
                           source=f"iao:{Path(arq).name}",
                           source_url=col(row, "fonte_qsa", "onde_achou_url", "email_fonte_url"),
                           external_id=("cnpj:" + re.sub(r"\D", "", col(row, "cnpj"))) if col(row, "cnpj") else "",
                           notes=col(row, "cnpj"), signals=sinais(row))
            cid = await state.add_company(comp)
            existente = await state.get_prospect_by_email(email)
            if existente:
                if RANK.get(st, 0) > RANK.get(existente.email_status, 0):
                    await state.update_prospect_email(existente.id, email, st)
                    atualizados += 1
                if email in manuais and existente.status in ("new", "queued"):
                    await state.update_prospect_status(existente.id, "contacted")
                    n = await state.cancel_pending_outbox_for_prospect(
                        existente.id, reason="ja contatado fora do Harvey (envios.csv)")
                    ja_contatados += 1
                    print(f"  {email}: ja contatado fora do Harvey; {n} item(ns) da fila cancelado(s)")
                continue
            p = Prospect(company_id=cid, first_name=first, last_name=last, email=email,
                         email_status=st, email_verified=(st == "verified"),
                         title=col(row, "decisor_cargo_qsa", "decisor_cargo", "cargo", "title") or "Sócio-Administrador",
                         seniority="c_suite", source=f"iao:{Path(arq).name}",
                         source_url=col(row, "onde_achou_url", "email_fonte_url", "fonte_qsa"),
                         personalization_notes=". ".join(notas), company=empresa,
                         industry=segmento(row),
                         status="contacted" if email in manuais else "new")
            await state.add_prospect(p)
            novos += 1
            ja_contatados += email in manuais
    print(f"importados {novos} novos, {atualizados} com status de email atualizado, "
          f"{ja_contatados} ja contatados fora do Harvey (ficam fora da escrita), {pulados} linhas puladas")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("arquivos", nargs="+")
    ap.add_argument("--seco", action="store_true", help="so lista o que importaria")
    a = ap.parse_args()
    asyncio.run(importa(a.arquivos, a.seco))


if __name__ == "__main__":
    main()
