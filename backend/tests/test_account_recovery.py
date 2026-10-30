"""계정 복구 순수 로직과 메일 발송기 단위 테스트(DB·네트워크 없음): 토큰 해시·만료, 이메일 정규화, 링크 fragment, 발송기 선택·로그."""

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest

from app import account_recovery as R
from app import mailer as M
from app.mailer import get_mailer as real_get_mailer  # conftest가 테스트마다 mailer.get_mailer를 가짜로 바꾸기 전의 원래 함수

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
SMTP_DUMMY_SECRET = "smtp-dummy-not-a-real-secret"


class TestTokens:
    def test_new_token_is_random_and_only_the_hash_is_derived(self):
        raw, digest = R.new_token()
        assert len(raw) >= 43 and digest == hashlib.sha256(raw.encode()).hexdigest() and len(digest) == 64
        assert raw not in digest and R.hash_token(raw) == digest
        assert len({R.new_token()[0] for _ in range(200)}) == 200

    def test_ttl(self):
        assert R.expires_at(R.PURPOSE_VERIFY_EMAIL, NOW) == NOW + timedelta(hours=24)
        assert R.expires_at(R.PURPOSE_RESET, NOW) == NOW + timedelta(minutes=30)

    def test_usable_boundaries(self):
        exp = NOW + timedelta(minutes=30)
        assert R.is_usable("reset_password", "reset_password", exp, None, NOW)
        assert not R.is_usable("reset_password", "reset_password", exp, None, exp)  # 만료 시각 그 자체는 만료
        assert not R.is_usable("reset_password", "reset_password", exp, NOW, NOW)  # 이미 씀
        assert not R.is_usable("verify_email", "reset_password", exp, None, NOW)  # 다른 목적


class TestEmail:
    @pytest.mark.parametrize("raw, want", [
        ("  Foo.Bar+x@Example.COM ", "foo.bar+x@example.com"),
        ("a@b.co", "a@b.co"),
        ("user_name-1@sub.domain.kr", "user_name-1@sub.domain.kr"),
    ])
    def test_normalized(self, raw, want):
        assert R.normalize_email(raw) == want

    @pytest.mark.parametrize("raw", [
        "", "   ", "plain", "a@b", "a@@b.com", "a b@c.com", "a@b..com", ".a@b.com", "a.@b.com", "a..b@c.com",
        "a@-b.com", "a@b-.com", "a@1.23", "a@b.com.", "한글@b.com", "a@한글.com", "a@b.com\nx", "a\t@b.com",
        "x" * 65 + "@b.com", "a@" + "b" * 250 + ".com",
    ])
    def test_rejected_without_echoing_input(self, raw):
        with pytest.raises(ValueError) as e:
            R.normalize_email(raw)
        if raw.strip():
            assert raw.strip() not in str(e.value)

    def test_non_string_rejected(self):
        with pytest.raises(ValueError):
            R.normalize_email(None)

    def test_max_length_254(self):
        local = "a" * 64
        domain = ".".join(["b" * 61] * 3) + ".com"  # 64 + 1 + 189 = 254
        email = f"{local}@{domain}"
        assert len(email) == 254 and R.normalize_email(email) == email
        with pytest.raises(ValueError):
            R.normalize_email("a" + email)

    @pytest.mark.parametrize("raw, want", [
        ("alice@example.com", "al***@ex***.com"), ("a@b.co", "a***@b***.co"), (None, "-"), ("", "-"),
    ])
    def test_mask(self, raw, want):
        assert R.mask_email(raw) == want


class TestLinksAndMail:
    def test_token_is_in_fragment_only(self):
        raw, _ = R.new_token()
        for purpose, path in [(R.PURPOSE_VERIFY_EMAIL, "/verify-email"), (R.PURPOSE_RESET, "/reset-password")]:
            u = urlsplit(R.link(purpose, raw))
            assert (u.scheme, u.netloc, u.path, u.query, u.fragment) == ("http", "localhost:5173", path, "", f"token={raw}")

    def test_base_url_from_env(self, monkeypatch):
        monkeypatch.setenv("FRONTEND_BASE_URL", "https://app.example.com/ ")
        assert R.link(R.PURPOSE_RESET, "t") == "https://app.example.com/reset-password#token=t"

    @pytest.mark.parametrize("make", [R.verify_mail, R.reset_mail])
    def test_mail_bodies_carry_the_token_only_in_the_fragment(self, make):
        raw, _ = R.new_token()
        m = make("a@b.com", "alice", raw)
        assert m.to == "a@b.com" and "alice" in m.body
        assert m.body.count(raw) == 1 and f"#token={raw}" in m.body and "?token" not in m.body and raw not in m.subject
        assert "요청한 적이 없다면 무시" in m.body

    def test_reset_mail_states_expiry_and_logout(self):
        m = R.reset_mail("a@b.com", "alice", "t")
        assert "30분" in m.body and "로그아웃" in m.body
        assert "24시간" in R.verify_mail("a@b.com", "alice", "t").body

    def test_username_mail_has_no_link(self):
        m = R.username_mail("a@b.com", "alice")
        assert "아이디: alice" in m.body and "http" not in m.body and "요청한 적이 없다면 무시" in m.body

    def test_recovery_message_does_not_depend_on_anything(self):
        assert "{" not in R.RECOVERY_ACCEPTED and "@" not in R.RECOVERY_ACCEPTED


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout):
        self.host, self.port, self.timeout = host, port, timeout
        self.calls = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context):
        self.calls.append(("starttls", context is not None))

    def login(self, user, password):
        self.calls.append(("login", user, password == SMTP_DUMMY_SECRET))

    def send_message(self, msg):
        self.calls.append(("send", msg["To"], msg["From"], msg["Subject"]))


class TestMailer:
    def test_file_mailer_without_smtp_settings(self, monkeypatch):
        monkeypatch.delenv("SMTP_HOST", raising=False)
        assert isinstance(real_get_mailer(), M.FileMailer)
        monkeypatch.setenv("SMTP_HOST", "   ")
        assert isinstance(real_get_mailer(), M.FileMailer)

    def test_smtp_mailer_with_settings(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_USER", "bot@example.com")
        monkeypatch.setenv("SMTP_PASSWORD", SMTP_DUMMY_SECRET)
        m = real_get_mailer()
        assert isinstance(m, M.SmtpMailer) and m.port == 587 and m.timeout == 10 and m.sender == "bot@example.com"
        assert SMTP_DUMMY_SECRET not in repr(m) and SMTP_DUMMY_SECRET not in str(vars(m).get("host"))
        monkeypatch.setenv("SMTP_PORT", "2525")
        monkeypatch.setenv("MAIL_FROM", "noreply@example.com")
        m = real_get_mailer()
        assert m.port == 2525 and m.sender == "noreply@example.com"
        monkeypatch.setenv("SMTP_PORT", "abc")
        assert real_get_mailer().port == 587

    def test_smtp_send_uses_starttls_login_and_timeout(self, monkeypatch):
        FakeSMTP.instances.clear()
        monkeypatch.setattr(M.smtplib, "SMTP", FakeSMTP)
        M.SmtpMailer("smtp.example.com", 587, "bot", SMTP_DUMMY_SECRET, "noreply@example.com").send(R.Mail("a@b.com", "제목", "본문"))
        s = FakeSMTP.instances[0]
        assert (s.host, s.port, s.timeout) == ("smtp.example.com", 587, 10)
        assert s.calls == [("starttls", True), ("login", "bot", True), ("send", "a@b.com", "noreply@example.com", "제목")]

    def test_smtp_failure_is_swallowed_and_logged_without_secrets(self, monkeypatch, caplog):
        class Boom(FakeSMTP):
            def login(self, user, password):
                raise RuntimeError(f"535 auth failed for {user} {password}")

        monkeypatch.setattr(M.smtplib, "SMTP", Boom)
        mail = R.reset_mail("alice@example.com", "alice", "tok-dummy-raw")
        with caplog.at_level(logging.WARNING):
            ok = M.send_safely(mail, M.SmtpMailer("smtp.example.com", 587, "bot", SMTP_DUMMY_SECRET, "noreply@example.com"))
        assert ok is False
        text = caplog.text
        assert "RuntimeError" in text and "al***@ex***.com" in text
        for secret in (SMTP_DUMMY_SECRET, "alice@example.com", "tok-dummy-raw", "535", mail.body):
            assert secret not in text

    def test_file_mailer_appends_full_mail_and_prints_only_masked(self, tmp_path, capsys):
        path = tmp_path / "dev_mail.log"
        fm = M.FileMailer(path)
        fm.send(R.verify_mail("alice@example.com", "alice", "raw-dummy-token-1"))
        fm.send(R.reset_mail("alice@example.com", "alice", "raw-dummy-token-2"))
        body = path.read_text(encoding="utf-8")
        assert "#token=raw-dummy-token-1" in body and "#token=raw-dummy-token-2" in body and body.count("=====") == 2
        out = capsys.readouterr().out
        assert "dev mail 기록됨" in out and "al***@ex***.com" in out
        assert "raw-dummy-token" not in out and "alice@example.com" not in out

    def test_dev_mail_log_is_under_gitignored_backups(self):
        assert M.DEV_MAIL_LOG.parent.name == "backups" and M.DEV_MAIL_LOG.name == "dev_mail.log"

    def test_tests_use_the_memory_outbox(self, mail_outbox):
        M.send_safely(R.Mail("a@b.com", "s", "b"))
        assert mail_outbox.outbox == [R.Mail("a@b.com", "s", "b")]
