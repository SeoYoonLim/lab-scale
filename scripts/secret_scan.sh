#!/usr/bin/env bash
# 저장소 시크릿 검사 진입점. 로컬과 CI(.github/workflows/ci.yml의 secret-scan job)가 같은 명령을 쓴다.
#   scripts/secret_scan.sh [--history RANGE] [--diff BASE] [--env-file backend/.env]
# 실제 검사는 같은 폴더의 secret_scan.py(표준 라이브러리만 사용, 외부 서비스 없음). 규칙과 허용 목록은 그 파일 머리말을 보라.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/.." && pwd)"
# 실제로 파이썬 코드를 돌릴 수 있는 인터프리터를 고른다(Windows의 python3는 Microsoft Store 스텁일 수 있다).
py=""
for cand in python3 python py; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >/dev/null 2>&1; then
    py="$cand"
    break
  fi
done
if [ -z "$py" ]; then
  echo "Python 3.8 이상이 필요합니다(python3 / python / py)." >&2
  exit 2
fi
cd "$root"
exec "$py" "$here/secret_scan.py" --root "$root" "$@"
