#!/usr/bin/env python3
"""pytest의 junit XML에서 실패/에러 테스트를 GitHub Actions 어노테이션(::error)으로 바꿔 출력한다.

    python scripts/ci_junit_annotations.py "$RUNNER_TEMP/junit.xml"

어노테이션은 Actions 실행 화면과 PR의 "Checks"에 바로 보이고, 로그를 내려받을 권한이 없어도 읽을 수 있다. 실패한 job의 `if: failure()` 단계에서 부른다.
GitHub는 단계당 어노테이션을 10개까지만 보여주므로 앞의 9개 테스트와 전체 요약 1개를 만든다. 출력은 실패 메시지의 앞부분뿐이고(테스트 출력 전체가 아님),
CI에서 쓰는 값은 모두 더미라 비밀이 섞이지 않는다.
"""

import sys
import xml.etree.ElementTree as ET

MAX_TESTS = 9
MAX_MESSAGE = 400


def _escape(text: str) -> str:
    """workflow command 데이터 이스케이프(%, 줄바꿈)."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_prop(text: str) -> str:
    return _escape(text).replace(":", "%3A").replace(",", "%2C")


def failures(xml_path: str) -> list[tuple[str, str]]:
    """[(테스트 이름, 실패 메시지)]. skipped는 포함하지 않는다(CI_FAIL_ON_SKIP이 skip을 failure로 바꾼다)."""
    out = []
    for tc in ET.parse(xml_path).getroot().iter("testcase"):
        for tag in ("failure", "error"):
            el = tc.find(tag)
            if el is not None:
                msg = (el.get("message") or "") + ("\n" + el.text.strip().splitlines()[-1] if el.text and el.text.strip() else "")
                out.append((f"{tc.get('classname')}::{tc.get('name')}", " ".join(msg.split())[:MAX_MESSAGE]))
                break
    return out


def annotations(xml_path: str) -> list[str]:
    found = failures(xml_path)
    lines = [f"::error title={_escape_prop(name)}::{_escape(msg)}" for name, msg in found[:MAX_TESTS]]
    if found:
        extra = f" (처음 {MAX_TESTS}개만 표시)" if len(found) > MAX_TESTS else ""
        lines.append(f"::error title=pytest 실패 요약::실패 {len(found)}개{extra}")
    return lines


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("사용법: ci_junit_annotations.py <junit.xml>", file=sys.stderr)
        return 2
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        lines = annotations(argv[0])
    except (OSError, ET.ParseError) as e:
        print(f"::warning title=junit 읽기 실패::{_escape(str(e))}")
        return 0
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
