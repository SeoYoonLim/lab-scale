"""DART 공시서류원본파일(document.xml) 수집 -> disclosure.content 채우기.

엔드포인트: GET https://opendart.fss.or.kr/api/document.xml?crtfc_key=...&rcept_no=...
성공하면 zip(안에 접수번호.xml)을 돌려주고, 실패하면 zip이 아니라 <result><status>..</status></result>
형태의 XML을 돌려준다(013 조회 없음, 014 파일 없음, 020 요청 제한 초과 등).

원문은 두 가지 형식이 섞여 있다: DART 자체 XML(dart4.xsd, 보고서류)과 HTML(공정공시/안내공시 서식).
둘 다 태그를 걷어내고 공백을 정리한 텍스트의 앞부분만 content에 저장한다(임베딩은 앞 500자만 쓴다).

재시작: content가 이미 채워진 행은 건너뛴다. 원문이 없는 등 되풀이해도 소용없는 실패는 상태 파일에 기록해
다음 실행에서 호출을 아낀다(--retry-failed로 다시 시도). 네트워크 오류 같은 일시 실패는 기록하지 않는다.
"""

import html
import io
import json
import os
import re
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import requests
from dotenv import load_dotenv

from app.db.session import SessionLocal
from app.models import Disclosure

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DART_DOCUMENT_URL = "https://opendart.fss.or.kr/api/document.xml"
CONTENT_MAX_LEN = 2000            # content 컬럼에 저장할 최대 글자 수
READ_LIMIT = 2 * 1024 * 1024       # 압축 해제 후 앞 2MB만 읽는다(투자설명서는 10MB가 넘는다)
DEFAULT_DELAY = 0.2                # 요청 간 대기(초). DART 일일 한도는 약 2만 건
RETRIES = 2
STATE_FILE = Path(__file__).resolve().parents[1] / "backups" / "disclosure_content_failures.json"

# 다시 시도해도 소용없는 DART 응답 코드(원문이 없음)
PERMANENT_STATUS = {"013", "014"}
QUOTA_STATUS = "020"

_STATUS_RE = re.compile(rb"<status>\s*(\d+)\s*</status>")
_RCPT_RE = re.compile(r"rcptNo=(\d+)")
_DROP_BLOCKS_RE = re.compile(r"(?is)<(style|script|head)\b.*?</\1>")
_COMMENT_RE = re.compile(r"(?s)<!--.*?-->")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_LEADING_VERSION_RE = re.compile(r"^\d+\.\d+\s")

# 보고서 표지의 정형 문구(수신처, 회사명~전화번호 블록, 홈페이지, 작성책임자). 공시마다 똑같아서 핵심 내용을 밀어내므로
# 저장 전에 걷어낸다. 표지가 다른 서식에서 본문을 삼키지 않도록 모두 길이를 제한한다.
_BOILERPLATE_RES = [
    re.compile(r"((금융위원회|금융감독원장|한국거래소|증권선물위원회)\s*/?\s*){1,3}(귀중|귀하)"),
    re.compile(r"회\s*사\s*명\s*:.{0,300}?\(\s*전\s*화\s*\)\s*[\d\- ]{6,15}"),
    re.compile(r"\(\s*홈페이지\s*\)\s*\S+"),
    re.compile(r"작\s*성\s*책\s*임\s*자\s*:.{0,100}?\(\s*전\s*화\s*\)\s*[\d\- ]{6,15}"),
    re.compile(r"tel\s*[\d\-]+\s*\(fax\)\s*[\d\-]+", re.I),
]


def strip_boilerplate(text: str) -> str:
    for pat in _BOILERPLATE_RES:
        text = pat.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


class QuotaExceeded(Exception):
    pass


@dataclass
class FetchOutcome:
    status: str                    # "ok" | "no_file" | "empty" | "transient"
    text: str = ""
    detail: str = ""


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "euc-kr"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def extract_text(raw: bytes, title: str = "") -> str:
    """원문 XML/HTML 바이트에서 태그를 걷어낸 본문 텍스트(앞 CONTENT_MAX_LEN자)를 만든다.

    보고서류 XML은 본문이 '문서명 버전 회사명 ...'으로 시작해서 제목과 겹치는 앞머리를 떼어낸다."""
    s = _decode(raw)
    s = _DROP_BLOCKS_RE.sub(" ", s)
    s = _COMMENT_RE.sub(" ", s)
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s).replace("\xa0", " ")
    s = _WS_RE.sub(" ", s).strip()
    # 앞머리의 문서명(제목과 공백만 다를 수 있음) + 서식 버전 번호 제거
    head = s[: len(title) + 20]
    if title and _WS_RE.sub("", head).startswith(_WS_RE.sub("", title)):
        n, seen = 0, 0
        target = len(_WS_RE.sub("", title))
        while seen < target and n < len(s):
            if not s[n].isspace():
                seen += 1
            n += 1
        s = s[n:].strip()
    s = _LEADING_VERSION_RE.sub("", s)
    return strip_boilerplate(s[: CONTENT_MAX_LEN * 2])[:CONTENT_MAX_LEN]


def fetch_document_text(rcept_no: str, title: str = "", session: requests.Session | None = None) -> FetchOutcome:
    """접수번호로 원문을 받아 본문 텍스트로 만든다. 호출 1회. QuotaExceeded는 호출측에서 처리한다."""
    key = os.environ["DART_API_KEY"]
    http = session or requests
    last = ""
    for attempt in range(RETRIES + 1):
        try:
            resp = http.get(DART_DOCUMENT_URL, params={"crtfc_key": key, "rcept_no": rcept_no}, timeout=60)
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            last = type(e).__name__
            if attempt == RETRIES:
                return FetchOutcome("transient", detail=last)
            time.sleep(1.5 * (attempt + 1))

    body = resp.content
    if not zipfile.is_zipfile(io.BytesIO(body)):
        m = _STATUS_RE.search(body)
        status = m.group(1).decode() if m else "?"
        if status == QUOTA_STATUS:
            raise QuotaExceeded()
        if status in PERMANENT_STATUS:
            return FetchOutcome("no_file", detail=status)
        return FetchOutcome("transient", detail=f"status={status}")

    try:
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            names = [i.filename for i in z.infolist() if not i.is_dir()]
            if not names:
                return FetchOutcome("empty", detail="빈 zip")
            with z.open(names[0]) as f:  # 접수번호.xml이 본문. 첨부가 함께 오면 첫 파일만 쓴다
                raw = f.read(READ_LIMIT)
    except zipfile.BadZipFile:
        return FetchOutcome("transient", detail="BadZipFile")

    text = extract_text(raw, title)
    if not text:
        return FetchOutcome("empty", detail="텍스트 없음")
    return FetchOutcome("ok", text=text)


def _load_state() -> dict[str, str]:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict[str, str]) -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=0), encoding="utf-8")


@dataclass
class CollectSummary:
    targets: int = 0
    calls: int = 0
    ok: int = 0
    no_file: int = 0
    empty: int = 0
    transient: int = 0
    skipped_known_failures: int = 0
    quota_hit: bool = False
    elapsed_s: float = 0.0
    samples: list = field(default_factory=list)


def collect_disclosure_content(
    limit: int | None = None,
    delay: float = DEFAULT_DELAY,
    dry_run: bool = True,
    retry_failed: bool = False,
    commit_every: int = 50,
) -> CollectSummary:
    """content가 비어 있는 공시의 원문을 수집해 저장한다. dry_run이면 호출은 하되 DB에는 쓰지 않는다."""
    started = time.time()
    summary = CollectSummary()
    state = {} if retry_failed else _load_state()
    db = SessionLocal()
    http = requests.Session()
    try:
        rows = (
            db.query(Disclosure)
            .filter(Disclosure.content.is_(None))
            .filter(Disclosure.source_url.isnot(None))
            .order_by(Disclosure.id)
            .all()
        )
        todo = []
        for r in rows:
            if str(r.id) in state:
                summary.skipped_known_failures += 1
            else:
                todo.append(r)
        if limit is not None:
            todo = todo[:limit]
        summary.targets = len(todo)

        pending = 0
        for i, row in enumerate(todo, 1):
            m = _RCPT_RE.search(row.source_url)
            if not m:
                state[str(row.id)] = "no_rcept_no"
                summary.no_file += 1
                continue

            summary.calls += 1
            try:
                out = fetch_document_text(m.group(1), row.title, http)
            except QuotaExceeded:
                summary.quota_hit = True
                print("DART 요청 제한(020)에 도달해 중단합니다. 내일 다시 실행하면 이어서 진행됩니다.")
                break

            if out.status == "ok":
                summary.ok += 1
                if len(summary.samples) < 3:
                    summary.samples.append((row.id, row.title, out.text[:80]))
                if not dry_run:
                    row.content = out.text
                    pending += 1
            elif out.status == "transient":
                summary.transient += 1
            else:
                setattr(summary, out.status, getattr(summary, out.status) + 1)
                state[str(row.id)] = f"{out.status}:{out.detail}"

            if not dry_run and pending >= commit_every:
                db.commit()
                _save_state(state)
                pending = 0
            if i % 100 == 0:
                print(
                    f"[{i}/{len(todo)}] 성공 {summary.ok} / 원문없음 {summary.no_file} / 빈본문 {summary.empty} "
                    f"/ 일시실패 {summary.transient} ({time.time() - started:.0f}s)",
                    flush=True,
                )
            time.sleep(delay)

        if not dry_run:
            db.commit()
            _save_state(state)
        return summary
    except Exception:
        db.rollback()
        if not dry_run:
            _save_state(state)
        raise
    finally:
        db.close()
        summary.elapsed_s = time.time() - started
