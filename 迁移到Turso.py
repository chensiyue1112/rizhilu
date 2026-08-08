"""
日知录记账 - 数据迁移脚本
将本地 SQLite (data.db) 迁移到 Turso 云数据库

用法：
  1. 在 Turso (https://turso.tech) 注册并创建数据库
  2. 获取数据库 URL 和 auth token
  3. 填入下方配置
  4. 运行: python 迁移到Turso.py
"""
import sqlite3, json, urllib.request, os

# ═══════════ 填入你的 Turso 信息 ═══════════
TURSO_URL = "https://rizhilu-chensiyue1112.aws-us-west-2.turso.io"
TURSO_TOKEN = "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3ODU0MjU2MjUsImlkIjoiMDE5ZmIzYTctY2YwMS03ZWJlLWI1YTAtOGE4YWU0YzYxN2Q1Iiwia2lkIjoiVkdJc2xqbDJTQTZ1THRLNmlQWWFOa3lzdnE3TVNLQ25GbVU3WExQYVE3cyIsInJpZCI6IjMxMGU5MzRhLTg3ZWItNDI2Zi04MjU3LWQxNDA3NDJmMmUxZiJ9.CI2vyKIG24u6v5unRMW3vVe6O5qIgqcBp2NtBuq7CVkCMf-TMtuJf-mlLzEZNm1Kd2goUBbFam5KFaKHM6w1Cw"
# ═══════════════════════════════════════════

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.db")
API = TURSO_URL.rstrip("/") + "/v2/pipeline"

def typed_args(values):
    """Convert Python values to Turso typed args"""
    result = []
    for v in values:
        if v is None:
            result.append({"type": "null"})
        elif isinstance(v, bool):
            result.append({"type": "integer", "value": 1 if v else 0})
        elif isinstance(v, int):
            result.append({"type": "integer", "value": str(v)})
        elif isinstance(v, float):
            result.append({"type": "float", "value": v})
        else:
            result.append({"type": "text", "value": str(v) if v is not None else ""})
    return result

def turso(sql, args=None):
    body = {"requests": [
        {"type": "execute", "stmt": {"sql": sql, "args": typed_args(args or [])}},
        {"type": "close"}
    ]}
    req = urllib.request.Request(API,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {TURSO_TOKEN}", "Content-Type": "application/json"},
        method="POST")
    resp = urllib.request.urlopen(req)
    data = json.loads(resp.read())
    results = [r for r in data.get("results", []) if r["type"] == "execute"]
    if results:
        r = results[0]["response"]["result"]
        return [dict(zip([c["name"] for c in r["cols"]], row)) for row in (r["rows"] or [])]
    return []

def main():
    if "你的数据库名" in TURSO_URL:
        print("请先在脚本顶部填入 TURSO_URL 和 TURSO_TOKEN")
        return

    print("创建表结构...")
    turso("""CREATE TABLE IF NOT EXISTS diary (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL, title TEXT DEFAULT '', content TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime')))""")
    turso("""CREATE TABLE IF NOT EXISTS income (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL, amount REAL NOT NULL,
        category TEXT DEFAULT '', note TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now','localtime')))""")
    turso("""CREATE TABLE IF NOT EXISTS expense (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL, amount REAL NOT NULL,
        category TEXT NOT NULL, description TEXT DEFAULT '',
        large INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now','localtime')))""")
    turso("""CREATE TABLE IF NOT EXISTS finance_snapshot (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL, liquid_assets REAL DEFAULT 0, debt REAL DEFAULT 0,
        stock REAL DEFAULT 0, fund REAL DEFAULT 0, convertible_bond REAL DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now','localtime')))""")
    turso("""CREATE TABLE IF NOT EXISTS electricity (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL, reading REAL NOT NULL,
        created_at TEXT DEFAULT (datetime('now','localtime')))""")
    turso("""CREATE TABLE IF NOT EXISTS mood (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL UNIQUE,
        rating INTEGER NOT NULL,
        note TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime')))""")
    turso("""CREATE TABLE IF NOT EXISTS weather (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL UNIQUE,
        weather TEXT NOT NULL,
        note TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime')))""")
    turso("""CREATE TABLE IF NOT EXISTS record (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        content TEXT NOT NULL,
        done INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime')))""")
    print("表结构创建完成")

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    BATCH = 50  # 每批最多 50 条 SQL

    for table in ["diary", "income", "expense", "finance_snapshot", "electricity", "mood", "weather", "record"]:
        rows = db.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
        if not rows:
            print(f"  {table}: 0 条，跳过")
            continue
        cols = [c[1] for c in db.execute(f"PRAGMA table_info({table})")]
        data_cols = [c for c in cols if c != "id"]
        col_names = ",".join(data_cols)
        ph = ",".join(["?"] * len(data_cols))

        total = len(rows)
        for i in range(0, total, BATCH):
            batch_rows = rows[i:i+BATCH]
            stmts = []
            for row in batch_rows:
                data = dict(row)
                vals = [data.get(c) for c in data_cols]
                stmts.append({"type": "execute", "stmt": {"sql": f"INSERT INTO {table} ({col_names}) VALUES ({ph})", "args": typed_args(vals)}})
            
            # Send in one pipeline call
            body = {"requests": stmts + [{"type": "close"}]}
            req = urllib.request.Request(API,
                data=json.dumps(body, ensure_ascii=False).encode(),
                headers={"Authorization": f"Bearer {TURSO_TOKEN}", "Content-Type": "application/json"},
                method="POST")
            urllib.request.urlopen(req)
            print(f"  {table}: {min(i+BATCH, total)}/{total}", end="\r")
        print(f"  {table}: {total} ok")

    db.close()
    print("\n迁移完成!")

if __name__ == "__main__":
    main()
