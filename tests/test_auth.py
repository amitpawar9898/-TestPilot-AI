from datetime import datetime, timedelta, timezone

from config.settings import Settings, login_required
from core import auth
from database.db import Database
from tools.fixer import function_source  # noqa: F401  (import check only)

PW = "correct horse 42"


def _db(tmp_path):
    return Database(tmp_path / "a.db")


def test_hashes_are_salted_and_verifiable():
    a, b = auth.hash_password(PW), auth.hash_password(PW)
    assert a != b and PW not in a
    assert auth.verify_password(PW, a) and not auth.verify_password(PW + "x", a)
    assert not auth.verify_password(PW, "garbage") and not auth.verify_password(PW, "scrypt$1$2")


def test_password_and_username_rules():
    assert auth.validate_password("short1") and auth.validate_password("alllettersnodigit") and auth.validate_password("1234567890123")
    assert auth.validate_password("password123") and auth.validate_password("aaaaaaaaaa1")
    assert auth.validate_password("alice-secret-99", "alice") and auth.validate_password(PW) is None
    assert auth.validate_username("a b") and auth.validate_username("ab") and auth.validate_username("ok_user.1") is None


def test_first_user_is_admin_and_usernames_are_unique_case_insensitively(tmp_path):
    db = _db(tmp_path)
    ok, _, u = auth.register(db, "Alice", PW)
    assert ok and u["role"] == "admin"
    ok2, msg, u2 = auth.register(db, "bob", PW)
    assert ok2 and u2["role"] == "user"
    assert not auth.register(db, "ALICE", PW)[0]
    assert not auth.register(db, "carol", PW, allow_signup=False)[0]
    assert auth.register(db, "carol", PW, allow_signup=False, by_admin=True, role="admin")[2]["role"] == "admin"


def test_login_success_failure_and_identical_error_messages(tmp_path):
    db = _db(tmp_path)
    auth.register(db, "alice", PW)
    assert auth.authenticate(db, "alice", PW)[0]
    wrong_pw = auth.authenticate(db, "alice", "nope-nope-1")
    unknown = auth.authenticate(db, "nobody", "nope-nope-1")
    assert not wrong_pw[0] and wrong_pw[1] == unknown[1] == auth.GENERIC_FAIL  # no account enumeration
    assert auth.authenticate(db, " alice ", PW)[0]  # surrounding spaces ignored


def test_account_locks_after_repeated_failures_and_unlocks(tmp_path):
    db = _db(tmp_path)
    auth.register(db, "alice", PW)
    for _ in range(auth.MAX_FAILS):
        auth.authenticate(db, "alice", "bad-bad-bad1")
    assert not auth.authenticate(db, "alice", PW)[0]  # correct password refused while locked
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
    with db._conn() as c:
        c.execute("UPDATE users SET locked_until = ?", (past,))
    assert auth.authenticate(db, "alice", PW)[0]


def test_change_password(tmp_path):
    db = _db(tmp_path)
    u = auth.register(db, "alice", PW)[2]
    assert not auth.change_password(db, u["id"], "wrong", "brand new pass 7")[0]
    assert not auth.change_password(db, u["id"], PW, "short")[0]
    assert auth.change_password(db, u["id"], PW, "brand new pass 7")[0]
    assert not auth.authenticate(db, "alice", PW)[0] and auth.authenticate(db, "alice", "brand new pass 7")[0]


def test_each_user_only_sees_their_own_data(tmp_path):
    db = _db(tmp_path)
    a = auth.register(db, "alice", PW)[2]["id"]
    b = auth.register(db, "bob", PW)[2]["id"]
    db.create_run("ra", "p", "o", "", [], {}, "none", "", user_id=a)
    db.create_run("rb", "p", "o", "", [], {}, "none", "", user_id=b)
    db.create_run("ranon", "p", "o", "", [], {}, "none", "")
    db.replace_results("ra", [{"case_id": "x", "status": "passed"}])
    db.replace_results("rb", [{"case_id": "x", "status": "failed"}, {"case_id": "y", "status": "failed"}])
    assert list(db.list_runs(user_id=a)["id"]) == ["ra"] and list(db.list_runs(user_id=b)["id"]) == ["rb"] and list(db.list_runs()["id"]) == ["ranon"]
    assert db.metrics(a)["passed"] == 1 and db.metrics(b)["failed"] == 2 and db.metrics()["runs"] == 1
    assert db.get_run("ra", user_id=b) is None and db.get_run("ra", user_id=a)["id"] == "ra"
    db.add_fix_memory("ra", "f", "p", "def f(): return 1", "def f(): return 2", user_id=a)
    assert len(db.list_fix_memory(user_id=a)) == 1 and db.list_fix_memory(user_id=b).empty and db.similar_fixes("def f return", 3, user_id=b) == []
    assert db.rate_fix_memory(int(db.list_fix_memory(user_id=a).iloc[0]["id"]), -1, user_id=b) == 0  # cannot touch another user's memory


def test_deleting_a_user_deletes_their_data(tmp_path):
    db = _db(tmp_path)
    a = auth.register(db, "alice", PW)[2]["id"]
    db.create_run("ra", "p", "o", "", [], {}, "none", "", user_id=a)
    db.replace_results("ra", [{"case_id": "x", "status": "passed"}])
    db.delete_user(a)
    assert db.get_run("ra") is None and db.metrics(a)["passed"] == 0 and db.count_users() == 0


def test_login_required_setting_and_env(monkeypatch=None):
    import os
    assert not login_required(Settings())
    assert login_required(Settings(require_login=True))
    os.environ["TESTPILOT_REQUIRE_LOGIN"] = "1"
    try:
        assert login_required(Settings())
    finally:
        del os.environ["TESTPILOT_REQUIRE_LOGIN"]


def test_session_expiry():
    assert auth.session_expired(0, 9 * 3600) and not auth.session_expired(0, 7 * 3600)
