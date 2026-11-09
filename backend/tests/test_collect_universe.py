"""scripts/collect_universe.py의 뉴스만 수집·이어하기·차단 시 중단. 네트워크·DB 없이 돈다."""

import json

import pytest

from app.collectors import CollectResult
from scripts import collect_universe as CU


@pytest.mark.parametrize("error, blocked", [
    ("TransientHTTPError: 429 Client Error", True), ("HTTPError: 403 Forbidden", True), ("HTTPError: 401 Unauthorized", True),
    ("TransientHTTPError: 500 Server Error", False), ("ConnectionError: 42900 port", False), (None, False), ("", False),
])
def test_is_blocked_error(error, blocked):
    assert CU.is_blocked_error(error) is blocked


@pytest.fixture
def universe(tmp_path, monkeypatch):
    path = tmp_path / "u.json"
    path.write_text(json.dumps({"universe": [{"rank": i, "ticker": f"00000{i}", "name": f"회사{i}"} for i in range(1, 4)]}), encoding="utf-8")
    monkeypatch.setattr(CU, "UNIVERSE_DIR", tmp_path)
    monkeypatch.setattr(CU, "_register", lambda *a, **k: None)
    monkeypatch.setattr(CU.time, "sleep", lambda s: None)

    class S:
        def execute(self, *a):
            return []

        def close(self):
            pass

    monkeypatch.setattr(CU, "SessionLocal", lambda: S())
    return tmp_path


def _run(monkeypatch, news, *args):
    monkeypatch.setattr(CU, "fetch_and_save_news", news)
    monkeypatch.setattr("sys.argv", ["x", "--universe", "u.json", *args])
    CU.main()


def test_news_only_never_calls_disclosures(universe, monkeypatch):
    def no_disclosures(*a, **k):
        raise AssertionError("공시를 부르면 안 된다")

    monkeypatch.setattr(CU, "fetch_and_save_disclosures", no_disclosures)
    seen = []
    _run(monkeypatch, lambda name, **k: seen.append(name) or CollectResult(ok=True), "--news-only")
    assert seen == ["회사1", "회사2", "회사3"]


def test_names_selects_only_those(universe, monkeypatch):
    seen = []
    _run(monkeypatch, lambda name, **k: seen.append(name) or CollectResult(ok=True), "--news-only", "--names", "회사2, 회사3")
    assert seen == ["회사2", "회사3"]


def test_blocked_stops_at_once_and_resume_skips_done(universe, monkeypatch):
    state = str(universe / "state.json")
    seen = []

    def blocked_on_second(name, **k):
        seen.append(name)
        return CollectResult(ok=False, error="TransientHTTPError: 429 Client Error") if name == "회사2" else CollectResult(ok=True)

    with pytest.raises(SystemExit) as e:
        _run(monkeypatch, blocked_on_second, "--news-only", "--state", state)
    assert e.value.code == CU.BLOCKED_EXIT_CODE and seen == ["회사1", "회사2"]

    seen.clear()
    _run(monkeypatch, lambda name, **k: seen.append(name) or CollectResult(ok=True), "--news-only", "--state", state)
    assert seen == ["회사2", "회사3"]


def test_failed_ticker_is_not_marked_done(universe, monkeypatch):
    state = universe / "state.json"
    _run(monkeypatch, lambda name, **k: CollectResult(ok=name != "회사1", error=None if name != "회사1" else "ConnectionError"), "--news-only", "--state", str(state))
    assert json.loads(state.read_text(encoding="utf-8"))["done"] == ["000002", "000003"]
