"""IA Operacional mailbox provider: Gmail API through a Service Account with
domain-wide delegation. No OAuth consent, no password, no token file.

Harvey does not own a sending path here. Every email goes out through the
IA Operacional sender script (``email_iao.py``, repo ``vendas``), so a Harvey
email is exactly what the team sends by hand:

- From: the configured alias (default kate@iaoperacional.com.br), with that
  person's signature (text + the single-block HTML) appended by the script;
- List-Unsubscribe, the per-sender DAILY LIMIT, the 60-180 s interval and the
  log line in ``areas/comercial/prospeccao/envios.csv`` (``prospeccao=True``);
- the founder-address lock and the unfilled-placeholder abort.

Replies are read from the operacao@ mailbox (the real user that owns the
aliases) with the same Service Account, read-only scope, limited to mail
addressed to the sender alias plus delivery-failure notices.

Settings (environment, all optional):

- ``HARVEY_IAO_VENDAS_SCRIPTS``: directory with email_iao.py
  (default ~/repos-iao/vendas/scripts)
- ``HARVEY_IAO_REMETENTE``: sender key in email_iao.REMETENTES (default kate)
- ``HARVEY_IAO_DRY_RUN=1``: build the full MIME, send nothing, return a failure
  ("dry-run") so the outbox item is never marked sent.

The credential stays where email_iao.py reads it (IAO_GOOGLE_SA). This module
never reads, copies or logs it.
"""

from __future__ import annotations

import asyncio
import csv
import importlib
import logging
import os
import re
import sys
import urllib.parse
from pathlib import Path

from harvey.integrations.gmail import GmailProvider
from harvey.integrations.mail_provider import InboundMessage, MailProvider, SendResult
from harvey.paths import PROJECT_ROOT

logger = logging.getLogger("harvey.iao_gmail_sa")

DEFAULT_SCRIPTS = "~/repos-iao/vendas/scripts"
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _truthy(value: str) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "sim", "on")


SIGNOFF_LINES = {"ia operacional", "comercial", "comercial | ia operacional", "--", "abraço", "abraços",
                 "att", "att.", "atenciosamente", "obrigada", "obrigado"}


def strip_signoff(body: str, names: set[str]) -> str:
    """Drop a sign-off the model added at the end ("Kate\\nIA Operacional"):
    email_iao.py appends the full signature, so it would show up twice."""
    lines = (body or "").rstrip().split("\n")
    drop = SIGNOFF_LINES | {n.lower() for n in names if n}
    while lines and (not lines[-1].strip()
                     or lines[-1].strip().rstrip(",.").lower() in drop):
        lines.pop()
    return "\n".join(lines).rstrip() or (body or "")


class IaoGmailSAProvider(MailProvider):
    name = "iao_gmail_sa"

    def __init__(self, config, env=None, environ=None):
        self.config = config
        environ = os.environ if environ is None else environ
        self.scripts_dir = Path(environ.get("HARVEY_IAO_VENDAS_SCRIPTS", DEFAULT_SCRIPTS)).expanduser()
        self.sender_key = environ.get("HARVEY_IAO_REMETENTE", "kate").strip() or "kate"
        self.dry_run = _truthy(environ.get("HARVEY_IAO_DRY_RUN", ""))
        # Message-IDs Harvey itself sent: a bounce only counts against Harvey
        # (kill switch) when it answers one of these, never a manual send.
        self.ledger = Path(environ.get(
            "HARVEY_IAO_LEDGER", str(PROJECT_ROOT / "data" / "iao_enviados.txt"))).expanduser()
        self._mod = None
        self.last_mime = ""  # dry-run: the MIME that would have gone out

    # ------------------------------------------------------------ plumbing
    def _email_iao(self):
        if self._mod is None:
            if str(self.scripts_dir) not in sys.path:
                sys.path.insert(0, str(self.scripts_dir))
            self._mod = importlib.import_module("email_iao")
        return self._mod

    @property
    def sender_address(self) -> str:
        return self._email_iao().remetente(self.sender_key)[1][1]

    def is_configured(self) -> bool:
        try:
            mod = self._email_iao()
            mod.remetente(self.sender_key)
            return Path(mod.SA).is_file()
        except Exception as e:  # import error, unknown sender
            logger.warning(f"iao_gmail_sa not configured: {e}")
            return False

    def remaining_today(self) -> int:
        """Sends left today under email_iao.py's limit for this sender. The
        limit counts every prospecting email of the sender (manual ones too),
        read from envios.csv, so Harvey never pushes the day past it."""
        try:
            lim, done = self._email_iao().limite(self.sender_key)
        except Exception as e:
            logger.error(f"iao_gmail_sa: could not read the daily limit: {e}")
            return 0
        return max(lim - done, 0)

    # ------------------------------------------------------------ send
    async def send_email(
        self,
        to_email: str,
        subject: str,
        body: str,
        thread_ref: str = "",
        in_reply_to: str = "",
    ) -> SendResult:
        mod = self._email_iao()
        names = {self.sender_key, mod.remetente(self.sender_key)[1][2]}
        persona = getattr(self.config, "persona", None)
        if persona is not None:
            names.add(getattr(persona, "name", ""))
        body = strip_signoff(body, names)
        if not in_reply_to:
            outro = self._manual_send_to(to_email)
            if outro:
                return SendResult(ok=False, error=(
                    f"já contatado fora do Harvey ({outro}); cold email do Harvey bloqueado"))
        try:
            result = await asyncio.to_thread(
                mod.enviar, self.sender_key, to_email, subject, body,
                seco=self.dry_run, prospeccao=True,
                em_resposta_a=in_reply_to or None, thread_id=thread_ref or None,
            )
        except mod.Erro as e:
            return SendResult(ok=False, error=f"email_iao: {e}"[:300])
        except Exception as e:
            logger.exception("iao_gmail_sa send failed")
            return SendResult(ok=False, error=f"iao_gmail_sa: {e}"[:300])
        if result.get("seco"):
            self.last_mime = result.get("mime", "")
            return SendResult(ok=False, error="dry-run: nada enviado (HARVEY_IAO_DRY_RUN=1)")
        message_id = result.get("message_id") or result.get("id", "")
        self._ledger_add(message_id)
        return SendResult(
            ok=True,
            message_id=message_id,
            thread_ref=result.get("threadId") or "",
        )

    # ------------------------------------------------------------ read
    def _list(self, query: str, limit: int) -> list[dict]:
        mod = self._email_iao()
        tk = mod.token([mod.S_READ])
        q = urllib.parse.quote(query)
        st, d = mod._http("GET", f"{mod.GMAIL}/messages?q={q}&maxResults={int(limit)}", tk)
        if st != 200:
            raise RuntimeError(f"messages.list -> HTTP {st}")
        out = []
        for stub in d.get("messages", [])[:limit]:
            st, full = mod._http("GET", f"{mod.GMAIL}/messages/{stub['id']}?format=full", tk)
            if st == 200:
                out.append(full)
        return out

    def _manual_send_to(self, recipient: str) -> str:
        """'<data> por <remetente>' when someone already wrote to ``recipient``
        outside Harvey (envios.csv row whose Message-ID isn't in the ledger):
        the manual flow and Harvey must never both cold-email the same person."""
        recipient = (recipient or "").strip().lower()
        try:
            with Path(self._email_iao().ENVIOS).open(encoding="utf-8", newline="") as f:
                rows = [r for r in csv.DictReader(f) if recipient and recipient in (r.get("para") or "").lower()]
        except OSError:
            return ""
        ours = self._ledger_ids()
        for r in rows:
            if (r.get("message_id") or "") not in ours:
                return f"{r.get('data_hora', '')[:16]} por {r.get('remetente', '')}"
        return ""

    def _sent_message_id(self, recipient: str) -> str:
        """Message-ID of the latest prospecting send to ``recipient`` (envios.csv),
        so a delivery-failure notice can be tied back to the outbox row."""
        try:
            with Path(self._email_iao().ENVIOS).open(encoding="utf-8", newline="") as f:
                rows = [r for r in csv.DictReader(f)
                        if recipient in (r.get("para") or "").lower()
                        and (r.get("remetente") or "").lower() == self.sender_address.lower()]
        except OSError:
            return ""
        rows.sort(key=lambda r: r.get("data_hora", ""))
        ours = self._ledger_ids()
        for r in reversed(rows):
            mid = r.get("message_id", "")
            if mid and mid in ours:
                return mid
        return ""

    def _ledger_ids(self) -> set[str]:
        try:
            return {l.strip() for l in self.ledger.read_text().splitlines() if l.strip()}
        except OSError:
            return set()

    def _ledger_add(self, message_id: str):
        if not message_id:
            return
        try:
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            with self.ledger.open("a") as f:
                f.write(message_id + "\n")
        except OSError as e:
            logger.warning(f"iao_gmail_sa: could not record sent Message-ID: {e}")

    def _fetch(self, limit: int) -> list[InboundMessage]:
        me = self.sender_address.lower()
        query = f"newer_than:7d in:inbox (to:{me} OR from:mailer-daemon OR from:postmaster)"
        messages = []
        for data in self._list(query, limit):
            msg = GmailProvider._parse_message(data)
            if msg is None or msg.from_email == me:
                continue
            if msg.is_bounce:
                if not msg.in_reply_to:
                    failed = [e.strip().lower() for e in
                              msg.headers.get("x-failed-recipients", "").split(",") if e.strip()]
                    if not failed:
                        failed = [e.lower() for e in EMAIL_RE.findall(msg.body or "")
                                  if not e.lower().endswith("iaoperacional.com.br")]
                    for addr in failed:
                        mid = self._sent_message_id(addr)
                        if mid:
                            msg.in_reply_to = mid
                            break
                if not msg.in_reply_to or msg.in_reply_to not in self._ledger_ids():
                    # A notice for a send that wasn't Harvey's (manual flow, other
                    # alias of the shared mailbox): counting it would skew Harvey's
                    # bounce-rate kill switch. email_iao.py bounces covers those.
                    continue
            messages.append(msg)
        return messages

    async def get_replies(self, limit: int = 50) -> list[InboundMessage]:
        return await asyncio.to_thread(self._fetch, limit)

    async def test_connection(self) -> tuple[bool, str]:
        if not self.is_configured():
            return False, "email_iao.py or the Service Account file is missing"
        try:
            mod = self._email_iao()
            st, d = await asyncio.to_thread(
                mod._http, "GET", f"{mod.GMAIL}/profile", mod.token([mod.S_READ]))
        except Exception as e:
            return False, f"token/profile failed: {e}"
        if st == 200 and d.get("emailAddress"):
            return True, (f"Mailbox {d['emailAddress']} via Service Account; sending as "
                          f"{self.sender_address}; {self.remaining_today()} send(s) left today")
        return False, f"profile HTTP {st}"
