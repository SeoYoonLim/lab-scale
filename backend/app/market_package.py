"""클라우드 수집 데이터 패키지(2026-11-23): 만들기·암호화·검증. GitHub Actions 수집기(scripts/cloud_market_data.py)가 만들고,
집 PC의 동기화(app/market_data_sync.py)가 검증한 뒤 DB에 넣는다.

## 왜 암호화하는가
- 패키지는 공개 저장소의 `market-data` 브랜치에 올린다(PC가 꺼져 있어도 보관되고, 집 PC는 이미 쓰는 SSH git fetch로 받는다).
- 원천(네이버 일별 시세·지수, Yahoo 환율, 네이버 뉴스 검색, DART)의 재배포 허용을 확인하지 못했다. 그래서 공개 브랜치에는
  암호문만 올리고, 내용은 키(GitHub Secret MARKET_DATA_KEY + 집 PC의 키 파일)를 가진 쪽만 읽는다. manifest(건수·날짜·체크섬)는 평문이다.

## 암호 방식(표준 라이브러리만, 새 의존성 없음)
- 마스터 키 32바이트 -> HMAC-SHA256으로 암호 키·인증 키를 나눈다.
- 암호화: HMAC-SHA256(암호 키, nonce || 블록 번호)를 키 스트림으로 쓰는 CTR 방식. 인증: HMAC-SHA256(인증 키, 머리글 || nonce || 암호문).
  (encrypt-then-MAC. 인증이 틀리면 복호화하지 않는다.)
- 평문은 gzip으로 줄인 JSON이다. manifest에는 평문(gzip 전 JSON)과 암호문 각각의 SHA-256을 적는다.

## 이 모듈이 하지 않는 것
- 네트워크·DB를 쓰지 않는다. 키를 출력하거나 manifest·로그에 넣지 않는다(manifest에는 키 SHA-256의 앞 8자리 key_id만).
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import json
import os
import secrets
from datetime import date, datetime
from pathlib import Path

SCHEMA_VERSION = 1
SCHEME = "hmac-sha256-ctr+hmac-sha256/v1"
MAGIC = b"LSMD1"  # 암호문 머리글
NONCE_BYTES = 16
TAG_BYTES = 32
SECTIONS = ("stocks", "etfs", "indices", "fx", "news", "disclosures")
SECTION_OK, SECTION_PARTIAL, SECTION_FAILED, SECTION_SKIPPED = "ok", "partial", "failed", "skipped"
KEY_ENV = "MARKET_DATA_KEY"
KEY_FILE_ENV = "MARKET_DATA_KEY_FILE"
DEFAULT_KEY_FILE = Path.home() / ".lab-scale" / "market_data_key"


class PackageError(Exception):
    """패키지가 불완전하거나 체크섬·스키마·인증이 맞지 않는다(정상 데이터로 쓰지 않는다)."""


# ---------- 키 ----------


def parse_key(text: str) -> bytes:
    raw = base64.b64decode(text.strip(), validate=True)
    if len(raw) != 32:
        raise PackageError("MARKET_DATA_KEY는 base64로 쓴 32바이트여야 한다")
    return raw


def load_key(env: dict | None = None) -> bytes | None:
    """환경변수 MARKET_DATA_KEY, 아니면 MARKET_DATA_KEY_FILE(기본 ~/.lab-scale/market_data_key). 없으면 None."""
    env = os.environ if env is None else env
    if env.get(KEY_ENV):
        return parse_key(env[KEY_ENV])
    path = Path(env.get(KEY_FILE_ENV) or DEFAULT_KEY_FILE)
    if path.is_file():
        return parse_key(path.read_text(encoding="ascii"))
    return None


def new_key_text() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:8]


def _subkeys(key: bytes) -> tuple[bytes, bytes]:
    return (hmac.new(key, b"lab-scale market-data enc", hashlib.sha256).digest(),
            hmac.new(key, b"lab-scale market-data mac", hashlib.sha256).digest())


def _keystream(enc_key: bytes, nonce: bytes, n: int) -> bytes:
    out = bytearray()
    block = 0
    while len(out) < n:
        out += hmac.new(enc_key, nonce + block.to_bytes(8, "big"), hashlib.sha256).digest()
        block += 1
    return bytes(out[:n])


def _xor(a: bytes, b: bytes) -> bytes:
    if not a:
        return b""
    return (int.from_bytes(a, "big") ^ int.from_bytes(b[:len(a)], "big")).to_bytes(len(a), "big")


def encrypt(plain: bytes, key: bytes, nonce: bytes | None = None) -> bytes:
    enc_key, mac_key = _subkeys(key)
    nonce = nonce or secrets.token_bytes(NONCE_BYTES)
    ks = _keystream(enc_key, nonce, len(plain))
    ct = _xor(plain, ks)
    tag = hmac.new(mac_key, MAGIC + nonce + ct, hashlib.sha256).digest()
    return MAGIC + nonce + tag + ct


def decrypt(blob: bytes, key: bytes) -> bytes:
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + NONCE_BYTES + TAG_BYTES:
        raise PackageError("암호문 형식이 아니다")
    enc_key, mac_key = _subkeys(key)
    p = len(MAGIC)
    nonce, tag, ct = blob[p:p + NONCE_BYTES], blob[p + NONCE_BYTES:p + NONCE_BYTES + TAG_BYTES], blob[p + NONCE_BYTES + TAG_BYTES:]
    if not hmac.compare_digest(tag, hmac.new(mac_key, MAGIC + nonce + ct, hashlib.sha256).digest()):
        raise PackageError("인증 실패(키가 다르거나 파일이 바뀌었다)")
    ks = _keystream(enc_key, nonce, len(ct))
    return _xor(ct, ks)


# ---------- 패키지 ----------


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _json_default(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    raise TypeError(f"JSON으로 쓸 수 없는 값: {type(v).__name__}")


def dumps(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default).encode("utf-8")


def section_file(name: str, data_date: date) -> str:
    return f"{name}_{data_date.isoformat()}.json.gz.enc"


def build(data_date: date, sections: dict[str, dict], key: bytes, *, generated_at: datetime, is_trading_day: bool,
          run: dict | None = None, package_version: int = 1) -> tuple[dict, dict[str, bytes]]:
    """sections: {이름: {"status", "rows", "targets", "succeeded", "failed", "failures", "source", "note"}}.
    (manifest, {파일 이름: 암호문}). rows가 없는 구역(skipped·failed)도 manifest에는 상태를 남긴다."""
    files: dict[str, bytes] = {}
    meta: dict[str, dict] = {}
    for name in SECTIONS:
        s = sections.get(name) or {"status": SECTION_SKIPPED, "rows": [], "note": "수집하지 않음"}
        rows = s.get("rows") or []
        entry = {k: s.get(k) for k in ("status", "source", "targets", "succeeded", "failed", "note") if s.get(k) is not None}
        entry["failures"] = (s.get("failures") or [])[:50]
        entry["rows"] = len(rows)
        dates = sorted({r["date"] for r in rows if r.get("date")})
        entry["date_range"] = [dates[0], dates[-1]] if dates else None
        if rows:
            plain = dumps({"schema_version": SCHEMA_VERSION, "section": name, "data_date": data_date.isoformat(), "rows": rows})
            blob = encrypt(gzip.compress(plain, mtime=0), key)
            fname = section_file(name, data_date)
            files[fname] = blob
            entry.update(file=fname, sha256_plain=sha256(plain), sha256_file=sha256(blob), bytes=len(blob))
        meta[name] = entry
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "package_version": package_version,
        "data_date": data_date.isoformat(),
        "is_trading_day": is_trading_day,
        "generated_at_kst": generated_at.isoformat(),
        "run": run or {},
        "encryption": {"scheme": SCHEME, "key_id": key_id(key)},
        "complete": all(meta[n]["status"] in (SECTION_OK, SECTION_SKIPPED) for n in SECTIONS),
        "sections": meta,
    }
    return manifest, files


def read_section(manifest: dict, name: str, blob: bytes, key: bytes) -> list[dict]:
    """manifest의 체크섬·스키마·기준일을 확인하고 rows를 돌려준다. 하나라도 틀리면 PackageError(그 구역만 버린다)."""
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise PackageError(f"스키마 버전이 다르다: {manifest.get('schema_version')}")
    entry = (manifest.get("sections") or {}).get(name) or {}
    if not entry.get("file"):
        raise PackageError(f"{name}: 파일 없음")
    if sha256(blob) != entry.get("sha256_file"):
        raise PackageError(f"{name}: 파일 체크섬 불일치")
    if (manifest.get("encryption") or {}).get("key_id") != key_id(key):
        raise PackageError(f"{name}: 키가 다르다(key_id 불일치)")
    plain = gzip.decompress(decrypt(blob, key))
    if sha256(plain) != entry.get("sha256_plain"):
        raise PackageError(f"{name}: 평문 체크섬 불일치")
    body = json.loads(plain)
    if body.get("schema_version") != SCHEMA_VERSION or body.get("section") != name or body.get("data_date") != manifest.get("data_date"):
        raise PackageError(f"{name}: 본문 머리글(스키마·구역·기준일)이 manifest와 다르다")
    rows = body.get("rows")
    if not isinstance(rows, list) or len(rows) != entry.get("rows"):
        raise PackageError(f"{name}: 행 수가 manifest와 다르다")
    return rows


def manifest_dir(data_date: date, run_tag: str) -> str:
    return f"packages/{data_date.isoformat()}/{run_tag}"
