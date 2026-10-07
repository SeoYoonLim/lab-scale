"""app/auth.py 순수 로직: 비밀번호 해시/검증, 토큰 발급/검증(만료·변조·alg 바꿔치기), 입력 규칙, JWT_SECRET 설정."""

import base64
import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app import auth
from app.auth import (
    ACCESS_TOKEN_TTL,
    InvalidToken,
    create_access_token,
    decode_access_token,
    get_jwt_secret,
    hash_password,
    normalize_username,
    validate_password,
    verify_password,
)


def _b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


class TestPasswordHash:
    def test_hash_is_argon2id_and_not_plaintext(self):
        h = hash_password("correct horse battery")
        assert h.startswith("$argon2id$")
        assert "correct horse battery" not in h

    def test_same_password_hashes_differently(self):
        assert hash_password("password123") != hash_password("password123")

    def test_verify(self):
        h = hash_password("password123")
        assert verify_password("password123", h) is True
        assert verify_password("password124", h) is False
        assert verify_password("", h) is False

    def test_broken_hash_is_false_not_exception(self):
        assert verify_password("password123", "not-a-hash") is False
        assert verify_password("password123", "$argon2id$v=19$m=65536,t=3,p=4$broken") is False


class TestToken:
    def test_roundtrip(self):
        assert decode_access_token(create_access_token(42)) == 42

    def test_claims(self, jwt_secret):
        now = datetime(2026, 10, 7, tzinfo=timezone.utc)
        token = create_access_token(7, now=now)
        claims = jwt.decode(token, jwt_secret, algorithms=["HS256"], options={"verify_exp": False})
        assert set(claims) == {"sub", "iat", "exp"}
        assert claims["sub"] == "7"
        assert claims["exp"] - claims["iat"] == ACCESS_TOKEN_TTL.total_seconds() == 24 * 3600
        assert jwt.get_unverified_header(token)["alg"] == "HS256"

    def test_expired_is_rejected(self):
        token = create_access_token(1, now=datetime.now(timezone.utc) - ACCESS_TOKEN_TTL - timedelta(seconds=1))
        with pytest.raises(InvalidToken):
            decode_access_token(token)

    def test_just_before_expiry_is_accepted(self):
        token = create_access_token(1, now=datetime.now(timezone.utc) - ACCESS_TOKEN_TTL + timedelta(minutes=1))
        assert decode_access_token(token) == 1

    def test_tampered_payload_is_rejected(self):
        header, _, sig = create_access_token(1).split(".")
        forged = _b64({"sub": "2", "iat": 1, "exp": 9999999999})
        with pytest.raises(InvalidToken):
            decode_access_token(f"{header}.{forged}.{sig}")

    def test_tampered_signature_is_rejected(self):
        token = create_access_token(1)
        flipped = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
        with pytest.raises(InvalidToken):
            decode_access_token(flipped)

    def test_other_secret_is_rejected(self):
        token = jwt.encode({"sub": "1", "iat": 1, "exp": 9999999999}, "x" * 48, algorithm="HS256")
        with pytest.raises(InvalidToken):
            decode_access_token(token)

    def test_alg_none_is_rejected(self):
        unsigned = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64({'sub': '1', 'iat': 1, 'exp': 9999999999})}."
        with pytest.raises(InvalidToken):
            decode_access_token(unsigned)

    def test_other_hmac_alg_is_rejected(self, jwt_secret):
        token = jwt.encode({"sub": "1", "iat": 1, "exp": 9999999999}, jwt_secret, algorithm="HS512")
        with pytest.raises(InvalidToken):
            decode_access_token(token)

    @pytest.mark.parametrize("missing", ["sub", "exp", "iat"])
    def test_missing_required_claim_is_rejected(self, jwt_secret, missing):
        claims = {"sub": "1", "iat": 1, "exp": 9999999999}
        del claims[missing]
        with pytest.raises(InvalidToken):
            decode_access_token(jwt.encode(claims, jwt_secret, algorithm="HS256"))

    def test_non_numeric_sub_is_rejected(self, jwt_secret):
        token = jwt.encode({"sub": "admin", "iat": 1, "exp": 9999999999}, jwt_secret, algorithm="HS256")
        with pytest.raises(InvalidToken):
            decode_access_token(token)

    @pytest.mark.parametrize("garbage", ["", "abc", "a.b.c", "Bearer x"])
    def test_garbage_is_rejected(self, garbage):
        with pytest.raises(InvalidToken):
            decode_access_token(garbage)

    def test_error_message_does_not_contain_token(self):
        token = create_access_token(1)[:-4] + "AAAA"
        with pytest.raises(InvalidToken) as e:
            decode_access_token(token)
        assert token not in str(e.value)


class TestJwtSecret:
    def test_missing_raises(self, monkeypatch):
        monkeypatch.delenv("JWT_SECRET")
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            get_jwt_secret()

    def test_short_raises_without_echoing_value(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET", "s" * (auth.MIN_SECRET_LEN - 1))
        with pytest.raises(RuntimeError, match="32자 이상") as e:
            get_jwt_secret()
        assert "s" * (auth.MIN_SECRET_LEN - 1) not in str(e.value)

    def test_exactly_min_len_is_ok(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET", "s" * auth.MIN_SECRET_LEN)
        assert get_jwt_secret() == "s" * auth.MIN_SECRET_LEN

    def test_token_cannot_be_issued_without_secret(self, monkeypatch):
        monkeypatch.delenv("JWT_SECRET")
        with pytest.raises(RuntimeError):
            create_access_token(1)


class TestUsernameRules:
    @pytest.mark.parametrize("raw, expected", [("abc", "abc"), ("Alice_01", "alice_01"), ("A" * 20, "a" * 20)])
    def test_valid_and_lowercased(self, raw, expected):
        assert normalize_username(raw) == expected

    @pytest.mark.parametrize(
        "raw", ["ab", "a" * 21, "", "has space", "dash-name", "한글아이디", "user@x", "ａｂｃ", "ı" * 3, "abc\n"]
    )
    def test_invalid(self, raw):
        with pytest.raises(ValueError) as e:
            normalize_username(raw)
        if raw:
            assert raw not in str(e.value)


class TestPasswordRules:
    @pytest.mark.parametrize("pw", ["a" * 8, "a" * 128, "한글비밀번호입니다", "with space 123"])
    def test_valid(self, pw):
        assert validate_password(pw) == pw

    @pytest.mark.parametrize("pw", ["", "a" * 7, "a" * 129])
    def test_invalid_without_echoing_value(self, pw):
        with pytest.raises(ValueError) as e:
            validate_password(pw)
        if pw:
            assert pw not in str(e.value)
