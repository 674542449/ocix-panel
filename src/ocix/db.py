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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ssh_keys (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT UNIQUE NOT NULL,
                public_key  TEXT NOT NULL,
                fingerprint TEXT,
                tier_data   TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            )
            """
        )
        _ensure_column(conn, "profiles", "tier_data", "TEXT")
        _ensure_column(conn, "audit_log", "ip", "TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts DESC)")


# ---- 账户等级持久化 ----
def set_account_tier(profile: str, tier_info: dict):
    import json
    with get_conn() as conn:
        conn.execute(
            "UPDATE profiles SET tier_data=? WHERE name=?",
            (json.dumps(tier_info, ensure_ascii=False), profile),
        )


def get_account_tier(profile: str) -> dict | None:
    import json
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT tier_data FROM profiles WHERE name=?", (profile,)).fetchone()
            if row and row["tier_data"]:
                return json.loads(row["tier_data"])
    except Exception:
        pass
    return None


def get_all_account_tiers() -> dict[str, dict]:
    import json
    res = {}
    try:
        with get_conn() as conn:
            rows = conn.execute("SELECT name, tier_data FROM profiles WHERE tier_data IS NOT NULL").fetchall()
            for r in rows:
                if r["tier_data"]:
                    try:
                        res[r["name"]] = json.loads(r["tier_data"])
                    except Exception:
                        pass
    except Exception:
        pass
    return res


# ---- 通用键值设置 ----
def get_setting(key: str, default=None):
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
    except sqlite3.OperationalError:
        return default


def set_setting(key: str, value: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


# ---- SSH 公钥池 ----
def list_ssh_keys():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, public_key, fingerprint, created_at FROM ssh_keys ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_ssh_key(key_id: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, public_key, fingerprint, created_at FROM ssh_keys WHERE id=?",
            (key_id,),
        ).fetchone()
    return dict(row) if row else None


def create_ssh_key(name: str, public_key: str, fingerprint: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO ssh_keys (name, public_key, fingerprint) VALUES (?, ?, ?)",
            (name.strip(), public_key.strip(), fingerprint.strip()),
        )
        return cur.lastrowid


def update_ssh_key(key_id: int, name: str, public_key: str, fingerprint: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE ssh_keys SET name=?, public_key=?, fingerprint=? WHERE id=?",
            (name.strip(), public_key.strip(), fingerprint.strip(), key_id),
        )


def delete_ssh_key(key_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM ssh_keys WHERE id=?", (key_id,))


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
# profiles 表允许写入的列。列名会拼进 SQL（占位符只能绑定值、绑不了列名），
# 因此必须白名单校验——否则将来有人不慎把用户数据当 kwargs 传进来就是注入。
_PROFILE_COLUMNS = {"user_ocid", "tenancy_ocid", "region", "fingerprint", "key_file"}


def upsert_profile(name, **fields):
    unknown = set(fields) - _PROFILE_COLUMNS
    if unknown:
        raise ValueError(f"profiles 表不存在这些列: {sorted(unknown)}")
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
