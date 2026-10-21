"""iao_gmail_sa provider: sends through email_iao.enviar, reads only mail for
the sender alias, ties bounces back via envios.csv, and caps the budget."""

import asyncio
import sys
import types

from harvey.integrations.iao_gmail_sa import IaoGmailSAProvider
from harvey.integrations.mail_provider import NATIVE_PROVIDERS, get_mail_provider


def _b64(s):
    import base64
    return base64.urlsafe_b64encode(s.encode()).decode()


def _fake_module(tmp_path, envios_rows=()):
    m = types.ModuleType("email_iao")

    class Erro(RuntimeError):
        pass

    m.Erro = Erro
    m.SA = tmp_path / "sa.json"
    m.SA.write_text("{}")
    m.ENVIOS = tmp_path / "envios.csv"
    m.ENVIOS.write_text(
        "data_hora,remetente,para,assunto,gmail_id,message_id,status,detalhe\n"
        + "".join(f"{r}\n" for r in envios_rows))
    m.GMAIL = "https://gmail.test/users/operacao"
    m.S_READ = "read"
    m.calls = []
    m.remetente = lambda k: (k, (k, f"{k}@iaoperacional.com.br", k))
    m.limite = lambda k: (60, 58)

    def enviar(de, para, assunto, corpo, **kw):
        m.calls.append((de, para, assunto, corpo, kw))
        if kw.get("seco"):
            return {"seco": True, "mime": "MIME"}
        if para == "limite@x.com":
            raise Erro("limite diario atingido")
        return {"id": "g1", "threadId": "t1", "message_id": "<m1@iao>"}

    m.enviar = enviar
    m.token = lambda escopos: "tok"
    m.messages = {}

    def _http(method, url, token=None, body=None, form=False):
        if "/messages?q=" in url:
            m.last_query = url
            return 200, {"messages": [{"id": i} for i in m.messages]}
        mid = url.rsplit("/messages/", 1)[1].split("?")[0]
        return 200, m.messages[mid]

    m._http = _http
    return m


def _msg(mid, frm, subject, body, extra=()):
    headers = [{"name": "From", "value": frm}, {"name": "Subject", "value": subject},
               {"name": "Message-ID", "value": f"<{mid}>"}] + list(extra)
    return {"id": mid, "threadId": "t" + mid,
            "payload": {"mimeType": "text/plain", "headers": headers, "body": {"data": _b64(body)}}}


def _provider(tmp_path, monkeypatch, mod, **env):
    monkeypatch.setitem(sys.modules, "email_iao", mod)
    env.setdefault("HARVEY_IAO_LEDGER", str(tmp_path / "ledger.txt"))
    p = IaoGmailSAProvider(config=None, environ=env)
    p._mod = mod
    return p


def test_registered_as_native():
    assert "iao_gmail_sa" in NATIVE_PROVIDERS
    cfg = types.SimpleNamespace(channels=types.SimpleNamespace(
        email=types.SimpleNamespace(provider="iao_gmail_sa")))
    assert isinstance(get_mail_provider(cfg, None), IaoGmailSAProvider)


def test_send_goes_through_email_iao_as_prospecting(tmp_path, monkeypatch):
    mod = _fake_module(tmp_path)
    p = _provider(tmp_path, monkeypatch, mod)
    assert p.is_configured()
    r = asyncio.run(p.send_email("a@x.com", "assunto", "corpo", thread_ref="t9", in_reply_to="<r@x>"))
    assert r.ok and r.message_id == "<m1@iao>" and r.thread_ref == "t1"
    assert p._ledger_ids() == {"<m1@iao>"}
    de, para, _, _, kw = mod.calls[0]
    assert (de, para) == ("kate", "a@x.com")
    assert kw["prospeccao"] is True and kw["seco"] is False
    assert kw["em_resposta_a"] == "<r@x>" and kw["thread_id"] == "t9"


def test_dry_run_never_marks_sent(tmp_path, monkeypatch):
    mod = _fake_module(tmp_path)
    p = _provider(tmp_path, monkeypatch, mod, HARVEY_IAO_DRY_RUN="1")
    r = asyncio.run(p.send_email("a@x.com", "s", "b"))
    assert not r.ok and "dry-run" in r.error and p.last_mime == "MIME"
    assert mod.calls[0][4]["seco"] is True


def test_limit_error_is_a_failed_send(tmp_path, monkeypatch):
    mod = _fake_module(tmp_path)
    p = _provider(tmp_path, monkeypatch, mod)
    r = asyncio.run(p.send_email("limite@x.com", "s", "b"))
    assert not r.ok and "limite" in r.error
    assert p.remaining_today() == 2


def test_replies_skip_own_mail_and_foreign_bounces(tmp_path, monkeypatch):
    mod = _fake_module(tmp_path, envios_rows=[
        "2026-10-20T10:00:00-03:00,kate@iaoperacional.com.br,lead@x.com,s,g,<sent1@iao>,enviado,",
    ])
    mod.messages = {
        "1": _msg("1", "Lead <lead@x.com>", "Re: s", "tenho interesse"),
        "2": _msg("2", "Kate <kate@iaoperacional.com.br>", "s", "minha propria"),
        "3": _msg("3", "Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
                  "Delivery Status Notification (Failure)", "falhou",
                  [{"name": "X-Failed-Recipients", "value": "lead@x.com"}]),
        "4": _msg("4", "Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
                  "Delivery Status Notification (Failure)", "falhou",
                  [{"name": "X-Failed-Recipients", "value": "outro@y.com"}]),
    }
    p = _provider(tmp_path, monkeypatch, mod)
    # a manual send's bounce is not Harvey's: dropped until the id is in the ledger
    assert [m.provider_id for m in asyncio.run(p.get_replies())] == ["1"]
    p._ledger_add("<sent1@iao>")
    got = asyncio.run(p.get_replies())
    assert [m.provider_id for m in got] == ["1", "3"]
    assert got[1].is_bounce and got[1].in_reply_to == "<sent1@iao>"
    assert "to%3Akate%40iaoperacional.com.br" in mod.last_query


def test_cold_email_refused_when_contacted_manually(tmp_path, monkeypatch):
    mod = _fake_module(tmp_path, envios_rows=[
        "2026-10-20T20:19:35-03:00,kate@iaoperacional.com.br,claudio@x.com,s,g,<manual@iao>,enviado,",
    ])
    p = _provider(tmp_path, monkeypatch, mod)
    r = asyncio.run(p.send_email("claudio@x.com", "s", "b"))
    assert not r.ok and "fora do Harvey" in r.error and not mod.calls
    # a reply in an existing conversation is not a cold email
    assert asyncio.run(p.send_email("claudio@x.com", "Re: s", "b", in_reply_to="<r@x>")).ok


def test_signoff_is_stripped_before_signature():
    from harvey.integrations.iao_gmail_sa import strip_signoff
    body = "Alfredo,\n\nComo vocês conferem o repasse?\n\nKate\nIA Operacional\n"
    assert strip_signoff(body, {"Kate"}) == "Alfredo,\n\nComo vocês conferem o repasse?"
    assert strip_signoff("Alfredo, tudo certo?", {"Kate"}) == "Alfredo, tudo certo?"


def test_gate_blocks_portuguese_spam_and_team_greeting():
    from harvey.gate import pre_send_check
    assert not pre_send_check("a@x.com", "assunto", "Olá, equipe da X. Oferta grátis.")
    assert pre_send_check("a@x.com", "repasse do mês", "Alfredo, como vocês conferem o repasse?")
