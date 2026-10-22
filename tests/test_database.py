from database.db import Database


def test_roundtrip_and_injection_safety(tmp_path):
    db = Database(tmp_path / "d.db")
    evil = "x'); DROP TABLE runs; --"
    db.create_run("r1", evil, "obj", "", [], {}, "none", "")
    db.replace_cases("r1", [{"id": "TC-001", "name": "n", "category": "normal", "function": "f", "args": "()", "kwargs": "{}", "expect": {"type": "no_exception"}}])
    db.replace_results("r1", [{"case_id": "TC-001", "status": "passed"}])
    assert db.get_run("r1")["project"] == evil
    assert db.metrics()["passed"] == 1 and len(db.list_runs(search=evil)) == 1


def test_source_not_stored_unless_retained(tmp_path):
    db = Database(tmp_path / "d.db")
    db.create_run("r1", "p", "", "", [], {}, "none", "")
    db.save_source("r1", {"filename": "a.py"}, {}, None)
    assert db.get_retained_source("r1") is None
    db.save_source("r1", {"filename": "a.py"}, {}, "print(1)")
    assert db.get_retained_source("r1") == "print(1)"
    assert db.scrub_retained_sources() == 1 and db.get_retained_source("r1") is None


def test_delete_cascades_and_purge(tmp_path):
    db = Database(tmp_path / "d.db")
    db.create_run("r1", "p", "", "", [], {}, "none", "")
    db.replace_results("r1", [{"case_id": "a", "status": "failed"}])
    db.delete_run("r1")
    assert db.metrics()["failed"] == 0
    db.create_run("r2", "p", "", "", [], {}, "none", "")
    assert db.purge_older_than(0) in (0, 1)
