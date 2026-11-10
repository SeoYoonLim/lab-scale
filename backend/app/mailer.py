"""메일 발송기: 인터페이스 하나(send)와 구현 셋.

- SmtpMailer: SMTP_HOST가 있으면 쓴다. SMTP_PORT(기본 587)에 STARTTLS(검증하는 기본 SSL 컨텍스트)로 붙고, SMTP_USER가 있으면 로그인한다.
  연결·전송 timeout 10초, 재시도 없음. 실패는 MailError로 감싸 올리고 호출 쪽(send_safely)이 삼킨다(API 응답은 같다).
- FileMailer: SMTP_HOST가 없을 때(개발) 자동 선택된다. backend/backups/dev_mail.log(gitignore 대상)에 메일 전체(링크 원문 포함)를
  덧붙이고, 콘솔에는 "dev mail 기록됨"과 마스킹한 수신자만 찍는다.
- MemoryMailer: 테스트용 메모리 보관함(tests/conftest.py가 모든 테스트에 기본으로 끼운다. 실제 파일·SMTP를 쓰지 않는다).

로그 규칙: 수신자는 mask_email로 가리고, 예외는 클래스 이름만 남긴다. SMTP 비밀번호·메일 본문·토큰은 어떤 로그에도 남기지 않는다.
"""

import logging
import os
import smtplib
import ssl
import threading
from email.message import EmailMessage
from datetime import datetime, timezone
from pathlib import Path

from app.account_recovery import Mail, mask_email

logger = logging.getLogger(__name__)

DEV_MAIL_LOG = Path(__file__).resolve().parents[1] / "backups" / "dev_mail.log"
SMTP_TIMEOUT_S = 10
DEFAULT_SMTP_PORT = 587


class MailError(Exception):
    """발송 실패(메시지에 비밀값·본문을 넣지 않는다)."""


class SmtpMailer:
    def __init__(self, host: str, port: int, user: str | None, password: str | None, sender: str, timeout: float = SMTP_TIMEOUT_S):
        self.host, self.port, self.user, self._password, self.sender, self.timeout = host, port, user, password, sender, timeout

    def __repr__(self) -> str:  # 비밀번호가 repr로 새지 않게
        return f"<SmtpMailer {self.host}:{self.port}>"

    def send(self, mail: Mail) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, mail.to, mail.subject
        msg.set_content(mail.body)
        try:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as s:
                s.starttls(context=ssl.create_default_context())
                if self.user:
                    s.login(self.user, self._password or "")
                s.send_message(msg)
        except Exception as e:  # 원문 예외에는 서버 응답·자격 증명 단서가 섞일 수 있어 클래스 이름만 남긴다
            raise MailError(type(e).__name__) from None


class FileMailer:
    def __init__(self, path: Path = DEV_MAIL_LOG):
        self.path = path
        self._lock = threading.Lock()

    def send(self, mail: Mail) -> None:
        stamp = datetime.now(timezone.utc).isoformat()
        entry = f"===== {stamp}\nTo: {mail.to}\nSubject: {mail.subject}\n\n{mail.body}\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(entry)
        print(f"dev mail 기록됨: {mask_email(mail.to)} ({self.path.name})")


class MemoryMailer:
    def __init__(self):
        self.outbox: list[Mail] = []

    def send(self, mail: Mail) -> None:
        self.outbox.append(mail)


def _port() -> int:
    try:
        return int(os.getenv("SMTP_PORT") or DEFAULT_SMTP_PORT)
    except ValueError:
        return DEFAULT_SMTP_PORT


def get_mailer():
    """SMTP_HOST가 있으면 SmtpMailer, 없으면 FileMailer. 환경변수는 호출할 때마다 읽는다(테스트가 바꿀 수 있게)."""
    host = (os.getenv("SMTP_HOST") or "").strip()
    if host:
        sender = (os.getenv("MAIL_FROM") or os.getenv("SMTP_USER") or "").strip()
        return SmtpMailer(host, _port(), (os.getenv("SMTP_USER") or "").strip() or None, os.getenv("SMTP_PASSWORD"), sender)
    return FileMailer()


def send_safely(mail: Mail, mailer=None) -> bool:
    """보내고 성공 여부를 돌려준다. 실패해도 예외를 밖으로 내지 않는다(API 응답이 달라지지 않게).

    같은 수신 주소로 시간당 RATE_LIMIT_MAIL_PER_RECIPIENT_PER_HOUR통(모든 메일 종류 합산)을 넘으면 보내지 않고 False(2026-10-30).
    계정을 여러 개 만들어 남의 주소로 인증 메일을 반복해 보내는 메일 폭탄을 막는다. 응답은 이미 나갔으므로 달라지지 않는다.
    """
    from app import rate_limit

    if rate_limit.enabled():
        ok, _ = rate_limit.mail_recipient.hit(mail.to.strip().lower(), rate_limit.setting("RATE_LIMIT_MAIL_PER_RECIPIENT_PER_HOUR"))
        if not ok:
            logger.warning("메일 발송 생략(수신 주소 시간당 한도): to=%s", mask_email(mail.to))
            return False
    try:
        (mailer or get_mailer()).send(mail)
        return True
    except Exception as e:
        name = e.args[0] if isinstance(e, MailError) and e.args else type(e).__name__
        logger.warning("메일 발송 실패: to=%s error=%s", mask_email(mail.to), name)
        return False
