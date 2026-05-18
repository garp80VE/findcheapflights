"""Email notifications via SMTP. Disabled when env vars are unset."""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tracker import Track

log = logging.getLogger("fcf.notifier")


def is_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER")
                and os.environ.get("SMTP_PASS"))


def send_alert_email(to_addr: str, track: "Track", price_usd: float, reason: str) -> None:
    if not is_configured():
        log.warning("SMTP not configured — skipping email")
        return

    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    pwd = os.environ["SMTP_PASS"]
    from_addr = os.environ.get("SMTP_FROM", user)

    msg = EmailMessage()
    msg["Subject"] = (
        f"✈ FindCheapFlights — {track.origin}→{track.destination} "
        f"a ${price_usd:.2f} ({reason})"
    )
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(
        f"Alerta de precio para tu ruta trackeada\n"
        f"=========================================\n\n"
        f"Ruta:       {track.origin} → {track.destination}\n"
        f"Ida:        {track.depart_date}\n"
        f"Vuelta:     {track.return_date or '(solo ida)'}\n"
        f"Pax:        {track.adults} adulto(s)"
        f"{f', {track.children} niño(s)' if track.children else ''}"
        f"{f', {track.infants} bebé(s)' if track.infants else ''}\n\n"
        f"Precio actual: ${price_usd:.2f} USD\n"
        f"Mínimo histórico: ${track.best_seen_usd or price_usd:.2f}\n"
        f"Tu objetivo: {f'${track.threshold_usd:.2f}' if track.threshold_usd else '(sin objetivo)'}\n\n"
        f"Motivo: {reason}\n\n"
        f"Abre la app para ver la lista completa y comprar:\n"
        f"http://127.0.0.1:8765/\n\n"
        f"--\n"
        f"FindCheapFlights price tracker"
    )

    log.info(f"sending alert email to {to_addr} via {host}:{port}")
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls()
        s.login(user, pwd)
        s.send_message(msg)
