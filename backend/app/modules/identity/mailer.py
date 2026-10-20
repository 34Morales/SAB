"""One-message-at-a-time SMTP worker for password-recovery outbox messages."""

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import smtplib
import ssl

from sqlalchemy import or_, select

from backend.app.core.config import get_settings
from backend.app.database.session import SessionLocal
from backend.app.modules.identity.models import RecoveryEmailOutbox
from backend.app.modules.identity.security import decrypt_secret


def deliver_one() -> bool:
    settings = get_settings()
    if not settings.smtp_host or not settings.smtp_from:
        raise RuntimeError("SMTP_HOST and SMTP_FROM must be configured")
    with SessionLocal.begin() as session:
        item = session.scalar(
            select(RecoveryEmailOutbox)
            .where(RecoveryEmailOutbox.sent_at.is_(None))
            .where(RecoveryEmailOutbox.expires_at > datetime.now(timezone.utc))
            .where(RecoveryEmailOutbox.attempts < 5)
            .where(or_(RecoveryEmailOutbox.next_attempt_at.is_(None), RecoveryEmailOutbox.next_attempt_at <= datetime.now(timezone.utc)))
            .where(or_(RecoveryEmailOutbox.claimed_at.is_(None), RecoveryEmailOutbox.claimed_at < datetime.now(timezone.utc) - timedelta(minutes=5)))
            .order_by(RecoveryEmailOutbox.id)
            .with_for_update(skip_locked=True)
        )
        if item is None:
            return False
        token = decrypt_secret(item.encrypted_token)
        item.claimed_at = datetime.now(timezone.utc)
        message = EmailMessage()
        message["Subject"] = "Restablece el acceso a ERP Empresarial"
        message["From"] = settings.smtp_from
        message["To"] = item.recipient
        reset_url = f"{settings.public_base_url.rstrip('/')}/api/v1/auth/password-reset-page#token={token}"
        message.set_content(
            "Se solicitó restablecer la contraseña de tu cuenta. "
            f"Abre este enlace dentro de 20 minutos: {reset_url}\n\n"
            "Si no solicitaste este cambio, ignora este correo."
        )
        item_id = item.id
        recipient = item.recipient
        encrypted = item.encrypted_token

    try:
        context = ssl.create_default_context()
        if settings.smtp_use_ssl:
            smtp = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20, context=context)
        else:
            smtp = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
        with smtp:
            if not settings.smtp_use_ssl:
                smtp.starttls(context=context)
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        with SessionLocal.begin() as session:
            item = session.get(RecoveryEmailOutbox, item_id)
            if item and item.sent_at is None and item.encrypted_token == encrypted:
                item.attempts += 1
                item.last_error = type(exc).__name__[:80]
                item.claimed_at = None
                if item.attempts < 5:
                    item.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=min(3600, 30 * 2 ** item.attempts))
                else:
                    item.recipient = ""
                    item.encrypted_token = ""
        return False

    with SessionLocal.begin() as session:
        item = session.get(RecoveryEmailOutbox, item_id)
        if item and item.sent_at is None and item.recipient == recipient:
            item.sent_at = datetime.now(timezone.utc)
            item.attempts += 1
            item.last_error = None
            item.claimed_at = None
            item.recipient = ""
            item.encrypted_token = ""
    return True


if __name__ == "__main__":
    for _ in range(20):
        if not deliver_one():
            break
