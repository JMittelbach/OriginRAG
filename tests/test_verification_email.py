from __future__ import annotations

from originrag.verification_email import LocalTestVerificationMailer, SMTPVerificationMailer


def test_smtp_mailer_uses_starttls_and_sends_only_to_recipient(monkeypatch) -> None:
    events: list[object] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            events.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def ehlo(self) -> None:
            events.append("ehlo")

        def starttls(self, *, context) -> None:
            events.append("starttls")

        def login(self, username: str, password: str) -> None:
            events.append(("login", username, password))

        def send_message(self, message) -> None:
            events.append(("message", message["To"], message.get_content()))

    monkeypatch.setattr("originrag.verification_email.smtplib.SMTP", FakeSMTP)
    mailer = SMTPVerificationMailer(
        host="smtp.bnitm.test",
        port=587,
        sender="pfempire@bnitm.de",
        username="relay-user",
        password="relay-secret",
    )

    mailer.send_verification_code("person@bnitm.de", "123456")

    assert "starttls" in events
    assert ("login", "relay-user", "relay-secret") in events
    message_event = next(item for item in events if isinstance(item, tuple) and item[0] == "message")
    assert message_event[1] == "person@bnitm.de"
    assert "123456" in message_event[2]


def test_smtp_mailer_is_disabled_without_host_and_sender(monkeypatch) -> None:
    for name in (
        "PFEMPIRE_SMTP_HOST",
        "PFEMPIRE_SMTP_FROM",
        "PFEMPIRE_SMTP_PORT",
        "PFEMPIRE_SMTP_SECURITY",
    ):
        monkeypatch.delenv(name, raising=False)

    assert SMTPVerificationMailer.from_env() is None


def test_local_test_mailer_reveals_code_once_without_persisting_it() -> None:
    mailer = LocalTestVerificationMailer()

    mailer.send_verification_code("person@bnitm.de", "123456")

    assert mailer.pop_test_code("person@bnitm.de") == "123456"
    assert mailer.pop_test_code("person@bnitm.de") == ""
