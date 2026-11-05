"""夜間の取り込みのドライバの「起動の予定」の試験（NIGHTLY_DRY_RUN=1・外にも DB にも触らない・ロックもログも作らない）。

起動元の選択: 筋ごと（top8・mtgo・mox）と後段（共起・毎日の監視）を、
どのリポジトリの脚本で動かすかを環境変数で選べる。月初の MTGO の前月分は、筋が有効なときだけ足す。
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DRIVER = os.path.join(REPO, "sh", "nightly_cron_driver.sh")


def _plan(tmp_path, **env):
    e = {k: v for k, v in os.environ.items() if not k.startswith("NIGHTLY_")}
    e.update({"NIGHTLY_DRY_RUN": "1", "LOG_DIR": str(tmp_path / "logs"), "STATE_DIR": str(tmp_path / "state")})
    e.update(env)
    r = subprocess.run(["bash", DRIVER], env=e, capture_output=True, text=True, timeout=30)
    lines = dict(l.split(": ", 1) for l in r.stdout.splitlines() if ": " in l)
    return r, lines


def _fake_repo(tmp_path, name):
    d = tmp_path / name
    (d / "sh").mkdir(parents=True)
    (d / "src").mkdir()
    for f in ("sh/nightly_scrape.sh", "src/build_cooccurrence.py", "src/daily_set_watch.py"):
        (d / f).write_text("")
    return d


def test_default_plan_uses_same_repo(tmp_path):
    r, p = _plan(tmp_path, NIGHTLY_DAY_OF_MONTH="15")
    assert r.returncode == 0, r.stderr
    for lane in ("top8", "mtgo", "mox"):
        assert p[lane].startswith(f"{REPO}/sh/nightly_scrape.sh "), (lane, p[lane])
    assert p["mtgo"].endswith(" mtgo:cur:all")
    assert p["後段"] == f"{REPO}/src/build_cooccurrence.py → {REPO}/src/daily_set_watch.py"
    assert not (tmp_path / "logs").exists(), "乾いた走りはログの置き場を作らない"


def test_month_start_adds_previous_month(tmp_path):
    r, p = _plan(tmp_path, NIGHTLY_DAY_OF_MONTH="02")
    assert r.returncode == 0
    jobs = p["mtgo"].split()[1:]
    assert len(jobs) == 2 and jobs[-1] == "mtgo:cur:all" and jobs[0].startswith("mtgo:20")


def test_disabled_mtgo_stays_disabled_at_month_start(tmp_path):
    r, p = _plan(tmp_path, NIGHTLY_DAY_OF_MONTH="01", NIGHTLY_JOBS_MTGO="")
    assert r.returncode == 0
    assert p["mtgo"] == "（無効）", "無効にした筋が月初だけ走らない"


def test_lane_and_post_repo_override(tmp_path):
    old = _fake_repo(tmp_path, "factory")
    r, p = _plan(tmp_path, NIGHTLY_DAY_OF_MONTH="15", NIGHTLY_LANE_REPO_MOX=str(old), NIGHTLY_POST_REPO=str(old))
    assert r.returncode == 0, r.stdout
    assert p["mox"].startswith(f"{old}/sh/nightly_scrape.sh ")
    assert p["top8"].startswith(f"{REPO}/sh/")
    assert p["後段"].startswith(f"{old}/src/build_cooccurrence.py")


def test_missing_lane_repo_stops(tmp_path):
    r, p = _plan(tmp_path, NIGHTLY_DAY_OF_MONTH="15", NIGHTLY_LANE_REPO_TOP8=str(tmp_path / "nowhere"))
    assert r.returncode == 1
    assert "起動元が見つからない" in r.stdout


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", *sys.argv[1:]]))
