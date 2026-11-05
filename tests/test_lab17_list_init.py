"""sh/lab17_import_sets.sh の 17Lands のセット一覧の用意の試験。

一覧の正本は状態の置き場の物で、無いときだけリポジトリの見本を写す。用意に失敗したら、
取り込み 0 件の「成功」で終わらせず、失敗の終了コードで止まる（DB にも触らない）。
外への取得をしないよう、どの場合も対象のセットを存在しない記号（ZZZ）に絞る。DB の照会は偽物に差し替える。
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "sh", "lab17_import_sets.sh")
SEED = "Test Set\tThu, 01 Jan 2026 00:00:00 GMT\thttps://example.invalid/d.csv.gz\thttps://example.invalid/g.csv.gz\n"


def _run(tmp_path, *, seed: bool, list_path=None, pre_list: str | None = None):
    repo = tmp_path / "repo"
    (repo / "sh").mkdir(parents=True)
    if seed:
        (repo / "sh" / "17lands_premier_sets.tsv").write_text(SEED)
    lst = list_path or (tmp_path / "state" / "17lands_premier_sets.tsv")
    if pre_list is not None:
        lst.parent.mkdir(parents=True, exist_ok=True)
        lst.write_text(pre_list)
    marker = tmp_path / "psql_called"
    stub = tmp_path / "psql_stub.sh"
    stub.write_text(f"#!/bin/sh\necho called >> '{marker}'\n")
    stub.chmod(0o755)
    env = {**os.environ, "REPO": str(repo), "L17_LIST": str(lst), "L17_LOG": str(tmp_path / "logs" / "l17.log"),
           "L17_REPORT_DIR": str(tmp_path / "logs" / "17lands"), "L17_DIR": str(tmp_path / "data"),
           "L17_PSQL": str(stub)}
    r = subprocess.run(["bash", SCRIPT, "ZZZ"], env=env, capture_output=True, text=True, timeout=60)
    return r, lst, marker


def test_first_run_creates_list_from_seed(tmp_path):
    r, lst, marker = _run(tmp_path, seed=True)
    assert r.returncode == 0, r.stderr
    assert lst.read_text() == SEED
    assert not list(lst.parent.glob("*.tmp.*")), "一時のファイルを残さない"


def test_existing_list_is_kept(tmp_path):
    r, lst, _ = _run(tmp_path, seed=True, pre_list="Kept Set\tx\ty\tz\n")
    assert r.returncode == 0, r.stderr
    assert lst.read_text() == "Kept Set\tx\ty\tz\n"


def test_missing_seed_stops_before_db(tmp_path):
    r, lst, marker = _run(tmp_path, seed=False)
    assert r.returncode != 0
    assert "見本" in r.stderr
    assert not lst.exists()
    assert not marker.exists(), "用意に失敗したら DB に触らない"


def test_unmakeable_list_dir_stops_before_db(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    r, lst, marker = _run(tmp_path, seed=True, list_path=blocker / "17lands_premier_sets.tsv")
    assert r.returncode != 0
    assert not marker.exists()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", *sys.argv[1:]]))
