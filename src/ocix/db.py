import sqlite3
from contextlib import contextmanager

from .config import DB_PATH


@contextmanager
def get_conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_column(conn, table: str, column: str, ddl: str):
    """给老版本建立的库补列，避免升级后启动失败。"""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db():
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                name         TEXT PRIMARY KEY,
                user_ocid    TEXT,
                tenancy_ocid TEXT,
                region       TEXT,
                fingerprint  TEXT,
                key_file     TEXT,
                created_at   TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                action   TEXT,
                profile  TEXT,
                target   TEXT,
                detail   TEXT,
                result   TEXT,
                ip       TEXT,
                ts       TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        _ensure_column(conn, "audit_log", "ip", "TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts DESC)")


# ---- 通用键值设置 ----
def get_setting(key: str, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


# ---- 审计日志 ----
def audit(username, action, profile="", target="", detail="", result="ok", ip=""):
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO audit_log (username, action, profile, target, detail, result, ip) "
                "VALUES (?,?,?,?,?,?,?)",
                (username, action, profile, target, detail, result, ip),
            )
    except Exception:
        # 审计失败不能影响主流程
        pass


def list_audit(limit: int = 50, offset: int = 0, action: str = "", result: str = "", profile: str = ""):
    where, params = [], []
    if action:
        where.append("action=?")
        params.append(action)
    if result:
        where.append("result=?")
        params.append(result)
    if profile:
        where.append("profile=?")
        params.append(profile)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) c FROM audit_log {clause}", params).fetchone()["c"]
        rows = conn.execute(
            f"SELECT id,username,action,profile,target,detail,result,ip,ts "
            f"FROM audit_log {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
    return [dict(r) for r in rows], total


def audit_actions():
    with get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT action FROM audit_log ORDER BY action").fetchall()
    return [r["action"] for r in rows]


def clear_audit(keep_days: int = 0) -> int:
    """keep_days=0 表示全部清空，否则只保留最近 N 天。返回删除条数。"""
    with get_conn() as conn:
        if keep_days > 0:
            cur = conn.execute(
                "DELETE FROM audit_log WHERE ts < datetime('now', ?)", (f"-{int(keep_days)} days",)
            )
        else:
            cur = conn.execute("DELETE FROM audit_log")
        return cur.rowcount or 0


# ---- profile 元数据 ----
def upsert_profile(name, **fields):
    cols = ["name"] + list(fields.keys())
    placeholders = ",".join(["?"] * len(cols))
    updates = ",".join(f"{c}=excluded.{c}" for c in fields.keys())
    sql = (
        f"INSERT INTO profiles ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(name) DO UPDATE SET {updates}"
    )
    with get_conn() as conn:
        conn.execute(sql, [name] + list(fields.values()))


def list_profiles_db():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT name,user_ocid,tenancy_ocid,region,fingerprint,key_file,created_at "
            "FROM profiles ORDER BY name"
        ).fetchall()
    return [dict(r) for r in rows]


def delete_profile_db(name):
    with get_conn() as conn:
        conn.execute("DELETE FROM profiles WHERE name=?", (name,))
