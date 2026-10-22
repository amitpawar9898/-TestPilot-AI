import io
import zipfile
from pathlib import Path

from config.settings import Settings
from core.batch import batch_zip, pair_files, run_batch, summary_rows
from core.orchestrator import Orchestrator
from core.state import RunRequest
from database.db import Database
from tools.fixer import compact_context

ROOT = Path(__file__).resolve().parent.parent
GOOD = b"def add(a, b, bucket=[]):\n    bucket.append(a)\n    return a + b\n"


def test_pairing_and_problems():
    files, problems = pair_files([("a.py", GOOD), ("a_requirements.txt", b"add(1, 2) == 3"), ("b.py", b"\xff\xfe"), ("c.py", b"   "), ("../evil/d.py", GOOD), ("n.md", b"x")])
    assert [(n, r) for n, _, r in files] == [("a.py", "add(1, 2) == 3"), ("d.py", "")]  # path traversal stripped
    assert len(problems) == 2


def test_batch_runs_each_file_and_zips_fixes(tmp_path):
    db = Database(tmp_path / "t.db")
    o = Orchestrator(Settings(execution_mode="local_trusted", per_test_timeout=2, max_iterations=1), db)
    files = [("a.py", GOOD.decode(), "add(1, 2) == 3"), ("b.py", "import a\ndef f():\n    return a.add(1, 1)\n", ""), ("bad.py", "def f(:\n", "")]
    states = run_batch(o, files, RunRequest(code=""), on_file=None)
    assert [s.request.filename for s in states] == ["a.py", "b.py", "bad.py"]
    rows = {r["File"]: r for r in summary_rows(states)}
    assert rows["bad.py"]["Status"] == "blocked" and "a" in rows["b.py"]["Note"]
    z = zipfile.ZipFile(io.BytesIO(batch_zip(states)))
    assert "fixed/a.py" in z.namelist() and "summary.csv" in z.namelist() and "fixed/bad.py" not in z.namelist()
    assert "bucket=None" in z.read("fixed/a.py").decode()


def test_big_files_are_compacted_but_keep_target_functions():
    big = "import os\n" + "".join(f"def f{i}(a):\n    return a + {i}  # " + "x" * 60 + "\n\n" for i in range(300))
    c = compact_context(big, ["f7"])
    assert len(c) < len(big) / 2 and "return a + 7" in c and "return a + 200" not in c and "def f200(a):" in c
    assert compact_context("def f():\n    pass\n", ["f"]) == "def f():\n    pass\n"
