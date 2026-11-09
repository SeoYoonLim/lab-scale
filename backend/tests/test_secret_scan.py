"""scripts/secret_scan.py(저장소 루트) 단위 테스트: 오탐/미탐, 임시 git 저장소로 추적 파일·히스토리·diff·.env 값 대조, 셸 진입점.

샘플 비밀 문자열은 런타임에 조립한다(이 파일이 검사기에 걸리지 않게, 그리고 진짜 키처럼 보이는 리터럴을 저장소에 두지 않으려고).
backend/scripts 패키지와 이름이 겹치므로 루트 scripts/secret_scan.py는 경로로 직접 불러온다.
"""

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("secret_scan", ROOT / "scripts" / "secret_scan.py")
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)

JWT = "ey" + "J" + "hbGciOiJIUzI1NiJ9" + "." + "ey" + "J" + "zdWIiOiIxMjM0NTY3ODkwIn0" + "." + "abcdefghij1234567890"
BEARER = "Bearer " + "abcdefghijklmnopqrstuvwxyz0123456789"
PRIVKEY = "-----BEGIN " + "RSA PRIVATE KEY-----"


# ---------------- 한 줄 검사 ----------------

@pytest.mark.parametrize("line, rule", [
    (f'token = "{JWT}"', "jwt"),
    (f"curl -H 'Authorization: {BEARER}'", "bearer"),
    (PRIVKEY, "private-key"),
    ('password = "hunter2hunter2"', "assign-quoted"),
    ("DB_PASSWORD = 'Xk29s8dKq1zP'", "assign-quoted"),
    ('{"api_key": "sk_live_abcdef123456"}', "assign-quoted"),
    ("JWT_SECRET=abcd1234efgh5678", "assign-env"),
    ("export NAVER_CLIENT_SECRET=q8w7e6r5t4y3", "assign-env"),
    ("POSTGRES_PASSWORD: Xk29s8dKq1zP", "assign-env"),
    ("DATABASE_URL=postgresql://admin:Xk29s8dKq1zP@db.internal.example.org:5432/app", "url-credentials"),
])
def test_detects_secrets(line, rule):
    assert rule in S.scan_line(line)


@pytest.mark.parametrize("line", [
    "password = secrets.token_urlsafe(12)",  # 값이 아니라 호출
    "ACCESS_TOKEN_TTL = timedelta(hours=24)",
    "token = create_access_token(user)",
    'DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:15432/ai_investment"',  # 허용 목록: 로컬 개발 DB 기본값
    "      DATABASE_URL: postgresql+psycopg://postgres:postgres@127.0.0.1:5432/x",
    "sqlalchemy.url = driver://user:pass@localhost/dbname",  # alembic.ini 자리표시
    "POSTGRES_PASSWORD: postgres",  # docker-compose 개발 컨테이너 값
    "      JWT_SECRET: ${{ secrets.JWT_SECRET }}",
    "      CI_DB_PASSWORD: ci_dummy_pw",  # 더미 값
    "JWT_SECRET=<your-jwt-secret>",
    "NAVER_CLIENT_SECRET=your_client_secret_here",
    'f"Authorization: Bearer {token}"',
    "curl -H 'Authorization: Bearer <access_token>'",
    "-H 'Authorization: Bearer $TOKEN_VALUE_FROM_ENV'",
    "PASSWORD_MIN_LENGTH = 8",
    "def hash_password(password: str) -> str:",
    "eyJ is how every JWT header starts",  # 이름만 있고 3단 토큰이 아님
])
def test_ignores_non_secrets(line):
    assert S.scan_line(line) == []


def test_path_allowance_only_turns_off_the_quoted_assignment_rule():
    line = 'password = "wrong-password"'
    assert S.scan_line(line, "backend/app/auth.py") == ["assign-quoted"]
    assert S.scan_line(line, "backend/tests/test_auth_api.py") == []  # 테스트의 가짜 비밀번호
    assert S.scan_line(line, "backend/API.md") == []  # 문서 예시
    # 같은 경로에서도 다른 규칙은 그대로
    assert S.scan_line(f'x = "{JWT}"', "backend/tests/test_auth_api.py") == ["jwt"]
    assert S.scan_line(BEARER, "backend/API.md") == ["bearer"]


@pytest.mark.parametrize("path, rules", [
    (".env", ["tracked-env"]),
    ("backend/.env", ["tracked-env"]),
    ("backend/.env.production", ["tracked-env"]),
    ("backend/.env.example", []),  # 견본은 허용
    ("backend/evals/results/raw_after_2026-10-27.jsonl", ["tracked-artifact"]),
    ("backend/evals/results/summary_after_2026-10-27.json", []),
    ("backend_server.log", ["tracked-artifact"]),
    ("deploy/id_rsa", ["tracked-artifact"]),
    ("certs/server.pem", ["tracked-artifact"]),
    ("backend/app/agent.py", []),
])
def test_tracked_file_name_rules(path, rules):
    assert S.check_path(path) == rules


# ---------------- 임시 git 저장소 ----------------

def git(repo, *args):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args],
                       cwd=repo, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return r.stdout


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def commit(repo, files: dict[str, str], msg="c"):
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


def run(repo, *args, capsys):
    code = S.main(["--root", str(repo), *args])
    return code, capsys.readouterr().out


def test_clean_repo_passes(repo, capsys):
    code, out = run(repo, capsys=capsys)
    assert code == 0 and "통과" in out


def test_tracked_secret_file_and_env_are_found_without_printing_values(repo, capsys):
    commit(repo, {".env": "JWT_SECRET=abcd1234efgh5678\n", "src/a.py": f'KEY = "{JWT}"\n'})
    code, out = run(repo, capsys=capsys)
    assert code == 1
    assert ".env [tracked-env]" in out and "src/a.py:1 [jwt]" in out
    assert "abcd1234efgh5678" not in out and JWT not in out  # 값은 출력하지 않는다


def test_untracked_files_are_not_scanned(repo, capsys):
    (repo / ".env").write_text("JWT_SECRET=abcd1234efgh5678\n", encoding="utf-8")  # git add 안 함
    code, _ = run(repo, capsys=capsys)
    assert code == 0


def test_history_finds_a_secret_that_was_later_removed(repo, capsys):
    base = git(repo, "rev-parse", "HEAD").strip()
    commit(repo, {"cfg.txt": f"auth {BEARER}\n"}, "add secret")
    commit(repo, {"cfg.txt": "auth removed\n"}, "remove secret")
    assert run(repo, capsys=capsys)[0] == 0  # 지금 추적 파일에는 없다
    code, out = run(repo, "--history", f"{base}..HEAD", capsys=capsys)
    assert code == 1 and "cfg.txt:1 [bearer]" in out and "history" in out and BEARER not in out


def test_diff_mode_only_looks_at_added_lines(repo, capsys):
    commit(repo, {"old.txt": 'password = "oldsecretvalue1"\n'}, "pre-existing")
    base = git(repo, "rev-parse", "HEAD").strip()
    commit(repo, {"new.txt": "nothing here\n"}, "pr")
    assert run(repo, "--diff", base, capsys=capsys)[0] == 0  # 기존 줄은 PR 변경이 아니다
    commit(repo, {"added.txt": 'password = "newsecretvalue1"\n'}, "pr2")
    code, out = run(repo, "--diff", base, capsys=capsys)
    assert code == 1 and "added.txt:1 [assign-quoted]" in out and "old.txt" not in out


def test_diff_mode_flags_added_raw_results_file(repo, capsys):
    base = git(repo, "rev-parse", "HEAD").strip()
    commit(repo, {"backend/evals/results/raw_x.jsonl": "{}\n"}, "oops")
    code, out = run(repo, "--diff", base, capsys=capsys)
    assert code == 1 and "tracked-artifact" in out


def test_env_file_values_are_matched_but_never_printed(repo, tmp_path_factory, capsys):
    env = tmp_path_factory.mktemp("envdir") / "real.env"
    env.write_text("NAVER_CLIENT_ID=\nJWT_SECRET=Zq81xLm20pRt\nSHORT=abc\n", encoding="utf-8")
    commit(repo, {"docs/notes.md": "the key is Zq81xLm20pRt, oops\n"})
    code, out = run(repo, "--env-file", str(env), capsys=capsys)
    assert code == 1 and "docs/notes.md:1 [env-value#1]" in out
    assert "Zq81xLm20pRt" not in out
    assert S.env_values(env) == ["Zq81xLm20pRt"]  # 6자 미만(SHORT)과 빈 값은 대상이 아니다


# ---------------- 셸 진입점 ----------------

def _bash():
    git_exe = shutil.which("git")
    if git_exe:  # Windows: Git for Windows의 bash를 먼저(PATH의 bash는 WSL일 수 있다)
        candidate = Path(git_exe).resolve().parent.parent / "bin" / "bash.exe"
        if candidate.exists():
            return str(candidate)
    found = shutil.which("bash")
    assert found, "bash가 필요합니다(Git for Windows 또는 리눅스/맥)"
    return found


def test_shell_entrypoint_runs_the_scanner_on_this_repo():
    r = subprocess.run([_bash(), str(ROOT / "scripts" / "secret_scan.sh")], capture_output=True, text=True,
                       encoding="utf-8", cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "통과" in r.stdout


def test_shell_entrypoint_passes_options_through():
    r = subprocess.run([_bash(), str(ROOT / "scripts" / "secret_scan.sh"), "--help"], capture_output=True, text=True,
                       encoding="utf-8", cwd=ROOT)
    assert r.returncode == 0 and "--history" in r.stdout and "--env-file" in r.stdout


def test_env_value_check_skips_the_public_dev_db_default(repo, tmp_path_factory, capsys):
    """.env의 DATABASE_URL이 공개된 로컬 개발 기본값과 같으면 그 값이 README/세션 코드에 있어도 오탐으로 세지 않는다."""
    url = "postgresql+psycopg://postgres:postgres@localhost:15432/ai_investment"
    env = tmp_path_factory.mktemp("envdir2") / "real.env"
    env.write_text(f"DATABASE_URL={url}\n", encoding="utf-8")
    commit(repo, {"app/session.py": f'DB_URL = os.getenv("DATABASE_URL", "{url}")\n'})
    code, out = run(repo, "--env-file", str(env), capsys=capsys)
    assert code == 0
