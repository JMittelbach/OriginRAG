from __future__ import annotations

import os
import smtplib
import ssl
import threading
from dataclasses import dataclass
from email.message import EmailMessage


@dataclass(frozen=True)
class SMTPVerificationMailer:
    host: str
    port: int
    sender: str
    username: str = ""
    password: str = ""
    security: str = "starttls"
    timeout: int = 15

    @classmethod
    def from_env(cls) -> SMTPVerificationMailer | None:
        host = os.environ.get("PFEMPIRE_SMTP_HOST", "").strip()
        sender = os.environ.get("PFEMPIRE_SMTP_FROM", "").strip()
        if not host or not sender:
            return None
        security = os.environ.get("PFEMPIRE_SMTP_SECURITY", "starttls").strip().lower()
        if security not in {"starttls", "ssl"}:
            raise ValueError("PFEMPIRE_SMTP_SECURITY must be starttls or ssl.")
        default_port = 465 if security == "ssl" else 587
        return cls(
            host=host,
            port=int(os.environ.get("PFEMPIRE_SMTP_PORT", str(default_port))),
            sender=sender,
            username=os.environ.get("PFEMPIRE_SMTP_USERNAME", "").strip(),
            password=os.environ.get("PFEMPIRE_SMTP_PASSWORD", ""),
            security=security,
            timeout=max(5, int(os.environ.get("PFEMPIRE_SMTP_TIMEOUT", "15"))),
        )

    def send_verification_code(self, recipient: str, code: str) -> None:
        message = EmailMessage()
        message["Subject"] = "Your OriginRAG verification code"
        message["From"] = self.sender
        message["To"] = recipient
        message.set_content(
            "Your OriginRAG verification code is:\n\n"
            f"{code}\n\n"
            "It expires in 10 minutes. If you did not request this code, ignore this email.\n"
        )
        context = ssl.create_default_context()
        if self.security == "ssl":
            with smtplib.SMTP_SSL(
                self.host,
                self.port,
                timeout=self.timeout,
                context=context,
            ) as client:
                self._authenticate(client)
                client.send_message(message)
            return
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as client:
            client.ehlo()
            if self.security == "starttls":
                client.starttls(context=context)
                client.ehlo()
            self._authenticate(client)
            client.send_message(message)

    def _authenticate(self, client: smtplib.SMTP) -> None:
        if self.username:
            client.login(self.username, self.password)


class LocalTestVerificationMailer:
    """Loopback-only test delivery that never sends mail or persists a code."""

    def __init__(self) -> None:
        self._codes: dict[str, str] = {}
        self._lock = threading.Lock()

    def send_verification_code(self, recipient: str, code: str) -> None:
        with self._lock:
            self._codes[recipient] = code

    def pop_test_code(self, recipient: str) -> str:
        with self._lock:
            return self._codes.pop(recipient, "")
