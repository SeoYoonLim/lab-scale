#!/usr/bin/env python3
"""저장소 시크릿 검사. 외부 서비스 없이 git과 정규식만 쓴다. CI(secret-scan job)와 로컬이 같은 명령으로 돈다.

    scripts/secret_scan.sh                          # 추적 파일(git ls-files) 전체 검사
    scripts/secret_scan.sh --history origin/main..HEAD   # 그 범위 커밋들이 추가한 줄도 검사(히스토리/PR diff)
    scripts/secret_scan.sh --diff origin/main       # origin/main...HEAD diff에서 추가된 줄만 검사
    scripts/secret_scan.sh --env-file backend/.env  # (로컬 전용) .env의 실제 값이 추적 파일·히스토리에 있는지도 검사

찾으면 `파일:줄 [규칙]`만 출력하고(값은 출력하지 않는다) 종료 코드 1로 끝난다. 찾은 게 없으면 0.

검사 규칙
  tracked-env       .env 파일이 추적되고 있다 (.env.example 같은 견본은 허용)
  tracked-artifact  raw_*.jsonl(평가 원본 결과), *_server.log, 개인 키 파일이 추적되고 있다
  jwt               eyJ로 시작하는 JWT 형태(header.payload.signature)
  bearer            Bearer 뒤에 20자 이상 토큰
  private-key       -----BEGIN ... PRIVATE KEY-----
  assign-quoted     password/secret/api key/token 이름에 8자 이상 문자열 리터럴 대입  (password = "abcd1234")
  assign-env        KEY=값 / KEY: 값 꼴(.env, yaml, 문서)에서 이름에 PASSWORD/SECRET/API_KEY/TOKEN이 있고 값이 8자 이상
  url-credentials   scheme://user:pass@host 꼴의 비밀번호

허용 목록(ALLOW_*)과 이유는 아래에 있다. 새 허용을 늘릴 때는 이유를 주석으로 남긴다.
"""

import argparse
import fnmatch
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# ---------------- 허용 목록 ----------------

# 로컬 개발 DB 기본 계정. docker-compose.yml, backend/app/db/session.py의 기본 DATABASE_URL, README 예시에 공개돼 있는 값이고
# localhost에서만 열리는 개발용 컨테이너(ai_investment_db)의 값이다. 운영 비밀이 아니다.
ALLOW_URL_CREDENTIALS = re.compile(r"postgres(?:ql)?(?:\+\w+)?://postgres:postgres@(?:localhost|127\.0\.0\.1)\b")
# backend/alembic.ini의 자리표시 URL(driver://user:pass@localhost/dbname). 실제 URL은 alembic/env.py가 DATABASE_URL로 덮어쓴다.
ALLOW_URL_CREDENTIALS = re.compile(ALLOW_URL_CREDENTIALS.pattern + r"|\bdriver://user:pass@localhost/dbname\b")
# 테스트와 문서에는 가입/로그인 예시용 가짜 비밀번호 리터럴이 있다(API.md의 "correct-horse-battery", 테스트의 "wrong-password" 등).
# 이 경로에서는 `assign-quoted` 규칙만 끈다. JWT·Bearer·개인 키·URL 비밀번호·.env 값 대조는 그대로 적용된다.
ALLOW_PATH_RULES = {"assign-quoted": ("backend/tests/", "*.md")}
# docker-compose.yml의 POSTGRES_PASSWORD: postgres 와 같은 개발용 컨테이너 값.
ALLOW_VALUES = {"postgres"}
# 값이 이런 낱말을 포함하면 견본/더미로 본다(.env.example, 문서의 <your_key>, CI의 더미 비밀번호 등).
PLACEHOLDER_WORDS = ("example", "dummy", "changeme", "change-me", "your_", "your-", "yourkey", "placeholder", "xxxx", "****",
                     "redacted", "sample", "secret_here", "token_here", "<", ">", "...", "todo")
# 추적을 허용하는 .env 견본 이름
ALLOW_ENV_NAMES = (".env.example", ".env.sample", ".env.template")
# 검사 대상에서 뺄 파일: 검사기 자신(패턴이 예시로 들어 있다)과 그 테스트
SELF_PATHS = ("scripts/secret_scan.py", "scripts/secret_scan.sh", "backend/tests/test_secret_scan.py")
# 내용을 읽지 않을 확장자(바이너리)
BINARY_EXT = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".woff", ".woff2", ".ttf", ".zip", ".gz", ".pyc")

NAME = r"(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?key|private[_-]?key|token|client[_-]?secret)"

RULES = [
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}")),
    ("bearer", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY")),
    # 이름 뒤에 따옴표 문자열 리터럴. 변수 대입(password = secrets.token_urlsafe())이나 f-string 자리표시는 값이 아니라서 제외한다.
    ("assign-quoted", re.compile(r"""(?ix)\b[\w.]*""" + NAME + r"""[\w]*["']?\s*[:=]\s*[rbuf]?["']([^"'\s${}<>()]{8,})["']""")),
    # 따옴표 없는 KEY=값 / KEY: 값. 이름 전체가 대문자 환경변수 꼴이라야 한다(코드의 일반 변수와 구분).
    ("assign-env", re.compile(r"""(?m)^\s*(?:export\s+)?([A-Z][A-Z0-9_]*""" + NAME.upper().replace("[_-]", "[_]") +
                              r"""[A-Z0-9_]*)\s*[:=]\s*["']?([^\s"'#${}<>()]{8,})(?=[\s"'#,;]|$)""")),
    ("url-credentials", re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@\"']+:([^/\s:@\"'$<>{}]{3,})@")),
]


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    where: str = ""  # "" = 작업 트리, 아니면 커밋 해시/범위

    def __str__(self) -> str:
        loc = f"{self.path}:{self.line}" if self.line else self.path
        return f"{loc} [{self.rule}]" + (f" ({self.where})" if self.where else "")


def _is_placeholder(value: str) -> bool:
    low = value.lower()
    return low in ALLOW_VALUES or any(w in low for w in PLACEHOLDER_WORDS)


def check_path(path: str) -> list[str]:
    """추적 파일 이름만으로 판단하는 규칙. 걸린 규칙 이름 목록."""
    name = path.rsplit("/", 1)[-1]
    out = []
    if (name == ".env" or name.startswith(".env.")) and name not in ALLOW_ENV_NAMES:
        out.append("tracked-env")
    if fnmatch.fnmatch(name, "raw_*.jsonl") or fnmatch.fnmatch(name, "*_server.log") or name in ("id_rsa", "id_ed25519") \
            or name.endswith((".pem", ".p12", ".pfx")):
        out.append("tracked-artifact")
    return out


def _path_allowed(rule: str, path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(path.startswith(pat) or fnmatch.fnmatch(name, pat) for pat in ALLOW_PATH_RULES.get(rule, ()))


def scan_line(line: str, path: str = "") -> list[str]:
    """한 줄에서 걸린 규칙 이름 목록(허용 목록 적용 후)."""
    hits = []
    for rule, rx in RULES:
        if path and _path_allowed(rule, path):
            continue
        for m in rx.finditer(line):
            if rule == "url-credentials" and ALLOW_URL_CREDENTIALS.search(line):
                continue
            if rule in ("assign-quoted", "assign-env"):
                value = m.group(m.lastindex)
                if _is_placeholder(value):
                    continue
            if rule == "url-credentials" and _is_placeholder(m.group(1)):
                continue
            hits.append(rule)
            break
    return hits


def scan_text(path: str, text: str, where: str = "") -> list[Finding]:
    found = []
    for i, line in enumerate(text.splitlines(), start=1):
        for rule in scan_line(line, path):
            found.append(Finding(path, i, rule, where))
    return found


def env_values(env_file: Path) -> list[str]:
    """.env의 값 중 6자 이상인 것(값 자체는 출력하지 않는다)."""
    vals = []
    for raw in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        v = raw.split("=", 1)[1].strip().strip("\"'")
        if len(v) >= 6:
            vals.append(v)
    return vals


def scan_env_values(path: str, text: str, values: list[str], where: str = "") -> list[Finding]:
    found = []
    for i, line in enumerate(text.splitlines(), start=1):
        if ALLOW_URL_CREDENTIALS.search(line):  # 공개된 로컬 개발 DB 기본 URL이 .env에도 그대로 들어 있는 경우
            continue
        for n, v in enumerate(values, start=1):
            if v in line:
                found.append(Finding(path, i, f"env-value#{n}", where))
    return found


# ---------------- git ----------------

def _git(args: list[str], cwd: Path) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 실패: {r.stderr.strip()[:200]}")
    return r.stdout


def scan_tracked(root: Path, values: list[str]) -> list[Finding]:
    findings = []
    for path in _git(["ls-files"], root).splitlines():
        findings += [Finding(path, 0, rule) for rule in check_path(path)]
        if path in SELF_PATHS or path.lower().endswith(BINARY_EXT):
            continue
        fp = root / path
        if not fp.is_file():
            continue
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        findings += scan_text(path, text)
        if values:
            findings += scan_env_values(path, text, values)
    return findings


def scan_added_lines(root: Path, diff_args: list[str], values: list[str], where: str) -> list[Finding]:
    """git diff/log -p 출력에서 추가된 줄(+)만 검사한다. 파일 이름 규칙(tracked-env 등)은 추가된 파일에도 적용한다."""
    out = _git(diff_args, root)
    findings, path, lineno = [], "", 0
    for raw in out.splitlines():
        if raw.startswith("+++ b/"):
            path = raw[6:]
            findings += [Finding(path, 0, rule, where) for rule in check_path(path)]
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            lineno = int(m.group(1)) - 1 if m else 0
        elif raw.startswith("+") and not raw.startswith("+++"):
            lineno += 1
            if path in SELF_PATHS or path.lower().endswith(BINARY_EXT):
                continue
            line = raw[1:]
            findings += [Finding(path, lineno, rule, where) for rule in scan_line(line, path)]
            findings += [Finding(path, lineno, f.rule, where) for f in scan_env_values(path, line, values, where)]
        elif not raw.startswith("-"):
            lineno += 1
    return findings


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):  # --help도 한글이라 파싱 전에 UTF-8로 맞춘다(윈도우 로케일 인코딩 방지)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="저장소 시크릿 검사")
    ap.add_argument("--history", metavar="RANGE", help="이 커밋 범위(예: origin/main..HEAD)가 추가한 줄도 검사")
    ap.add_argument("--diff", metavar="BASE", help="BASE...HEAD diff의 추가된 줄만 검사(추적 파일 전체 검사 대신)")
    ap.add_argument("--env-file", metavar="PATH", help="(로컬) 이 .env의 실제 값이 들어 있는지도 검사. 값은 출력하지 않는다")
    ap.add_argument("--root", default=".", help="git 저장소 루트(기본: 현재 디렉터리)")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    values = env_values(Path(args.env_file)) if args.env_file else []
    findings: list[Finding] = []
    scanned = []
    if args.diff:
        findings += scan_added_lines(root, ["diff", "--unified=0", f"{args.diff}...HEAD"], values, f"diff {args.diff}...HEAD")
        scanned.append(f"diff {args.diff}...HEAD")
    else:
        findings += scan_tracked(root, values)
        scanned.append("추적 파일")
    if args.history:
        findings += scan_added_lines(root, ["log", "-p", "--unified=0", "--no-color", args.history], values, f"history {args.history}")
        scanned.append(f"커밋 {args.history}")
    for f in sorted(set(findings), key=lambda f: (f.path, f.line, f.rule)):
        print(f)
    if findings:
        print(f"\n시크릿 의심 {len(set(findings))}건 (검사: {', '.join(scanned)}). 값은 출력하지 않았다. "
              "진짜 비밀이면 폐기·교체하고, 견본/더미면 값에 example/dummy 등을 넣거나 scripts/secret_scan.py 허용 목록에 이유와 함께 추가한다.")
        return 1
    print(f"시크릿 검사 통과 (검사: {', '.join(scanned)}{', env 값 대조' if values else ''})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
