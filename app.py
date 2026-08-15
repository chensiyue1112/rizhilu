"""
日知录 · 本地记账系统
Flask + SQLite 后端，单 HTML 文件前端
启动: python app.py  →  浏览器打开 http://localhost:5000
"""
import json
import os
import sys
import sqlite3
import io
from datetime import datetime, date
from flask import Flask, request, jsonify, g, send_file

# ──────────── 配置 ────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data.db")

# ──────────── 读取前端 HTML（启动时一次性加载到内存） ────────────
_html_path = os.path.join(BASE_DIR, "index.html")
if os.path.exists(_html_path):
    with open(_html_path, "r", encoding="utf-8") as _f:
        INDEX_HTML = _f.read()
else:
    INDEX_HTML = "<h1>错误：找不到 index.html</h1><p>路径: " + _html_path + "</p>"

app = Flask(__name__, static_folder=None)


# ──────────── 数据库工具 ────────────
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db:
        db.close()


def init_db():
    """建表（如果不存在）"""
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS diary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            title TEXT DEFAULT '',
            content TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS income (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT DEFAULT '日常收入',
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS expense (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            description TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS finance_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            liquid_assets REAL DEFAULT 0,
            debt REAL DEFAULT 0,
            stock REAL DEFAULT 0,
            fund REAL DEFAULT 0,
            convertible_bond REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS electricity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            reading REAL NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS mood (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            rating INTEGER DEFAULT 3,
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS weather (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            weather TEXT DEFAULT '',
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS record (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT DEFAULT '',
            content TEXT NOT NULL,
            done INTEGER DEFAULT 0,
            due_date TEXT DEFAULT '',
            followup TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
    """)
    # 老库升级：补充大额标记列（CREATE TABLE IF NOT EXISTS 不会改旧表结构）
    _ensure_column(db, "income", "large", "INTEGER DEFAULT 0")
    _ensure_column(db, "expense", "large", "INTEGER DEFAULT 0")
    db.commit()
    db.close()


def _ensure_column(db, table, col, decl):
    """为已有表添加缺失的列（幂等）"""
    cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in cols:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


# ──────────── 通用 CRUD 帮助函数 ────────────
TABLES = {
    "diary": {
        "cols": ["date", "title", "content"],
        "required": ["date"],
    },
    "income": {
        "cols": ["date", "amount", "category", "note", "large"],
        "required": ["date", "amount"],
    },
    "expense": {
        "cols": ["date", "amount", "category", "description", "large"],
        "required": ["date", "amount", "category"],
    },
    "finance_snapshot": {
        "cols": ["date", "liquid_assets", "debt", "stock", "fund", "convertible_bond"],
        "required": ["date"],
    },
    "electricity": {
        "cols": ["date", "reading"],
        "required": ["date", "reading"],
    },
    "mood": {
        "cols": ["date", "rating", "note"],
        "required": ["date"],
    },
    "weather": {
        "cols": ["date", "weather", "note"],
        "required": ["date"],
    },
    "record": {
        "cols": ["date", "content", "done", "due_date", "followup"],
        "required": ["content"],
    },
}

# 数值字段（新增/更新时统一转 float）
NUM_COLS = {
    "amount", "reading", "liquid_assets", "debt", "stock", "fund",
    "convertible_bond", "rating", "done", "large",
}


def _list(table):
    """GET 列表"""
    db = get_db()
    order = "DESC"
    # 财务快照按日期倒序看最新的
    rows = db.execute(
        f"SELECT * FROM {table} ORDER BY date {order}, id {order}"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


def _get(table, id):
    """GET 单条"""
    db = get_db()
    row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (id,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(row))


def _create(table):
    """POST 新增"""
    data = request.get_json(silent=True) or {}
    info = TABLES[table]

    # 检查必填
    for col in info["required"]:
        if col not in data or data[col] == "" or data[col] is None:
            return jsonify({"error": f"缺少必填字段: {col}"}), 400

    # 收集字段
    values = {}
    for col in info["cols"]:
        values[col] = data.get(col, "" if col != "amount" and col != "reading" else 0)
        # 数值字段默认 0
        if col in NUM_COLS:
            try:
                values[col] = float(values[col]) if values[col] != "" else 0
            except (ValueError, TypeError):
                values[col] = 0

    cols_str = ", ".join(values.keys())
    placeholders = ", ".join(["?"] * len(values))
    vals = list(values.values())

    db = get_db()
    cur = db.execute(f"INSERT INTO {table} ({cols_str}) VALUES ({placeholders})", vals)
    db.commit()
    new_id = cur.lastrowid

    row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (new_id,)).fetchone()
    return jsonify(dict(row)), 201


def _update(table, id):
    """PUT 更新"""
    data = request.get_json(silent=True) or {}
    info = TABLES[table]

    set_clauses = []
    vals = []
    for col in info["cols"]:
        if col in data:
            set_clauses.append(f"{col} = ?")
            v = data[col]
            if col in NUM_COLS:
                try:
                    v = float(v) if v != "" else 0
                except (ValueError, TypeError):
                    v = 0
            vals.append(v)

    if not set_clauses:
        return jsonify({"error": "没有要更新的字段"}), 400

    # 日记自动更新 updated_at
    if table == "diary":
        set_clauses.append("updated_at = datetime('now','localtime')")

    vals.append(id)
    db = get_db()
    db.execute(f"UPDATE {table} SET {', '.join(set_clauses)} WHERE id = ?", vals)
    db.commit()

    row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (id,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(row))


def _delete(table, id):
    """DELETE 删除"""
    db = get_db()
    row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (id,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    db.execute(f"DELETE FROM {table} WHERE id = ?", (id,))
    db.commit()
    return jsonify({"deleted": id})


# ──────────── 注册路由 ────────────
def _register_routes():
    for table in TABLES:
        # 用唯一的 endpoint 名称避免 Flask 冲突
        def make_list_handler(t):
            def handler():
                if request.method == "GET":
                    return _list(t)
                return _create(t)
            return handler

        def make_one_handler(t):
            def handler(id):
                if request.method == "GET":
                    return _get(t, id)
                if request.method == "PUT":
                    return _update(t, id)
                return _delete(t, id)
            return handler

        app.add_url_rule(
            f"/api/{table}",
            endpoint=f"list_{table}",
            view_func=make_list_handler(table),
            methods=["GET", "POST"],
        )
        app.add_url_rule(
            f"/api/{table}/<int:id>",
            endpoint=f"one_{table}",
            view_func=make_one_handler(table),
            methods=["GET", "PUT", "DELETE"],
        )

_register_routes()


# ──────────── 导入导出 ────────────
@app.route("/api/export")
def export_all():
    """导出所有数据为 JSON 文件"""
    db = get_db()
    package = {
        "version": 1,
        "exported_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "tables": {},
    }
    for table in TABLES:
        rows = db.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
        package["tables"][table] = [dict(r) for r in rows]

    json_str = json.dumps(package, ensure_ascii=False, indent=2)
    return send_file(
        io.BytesIO(json_str.encode("utf-8")),
        mimetype="application/json",
        as_attachment=True,
        download_name=f"日知录备份_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
    )


@app.route("/api/import", methods=["POST"])
def import_all():
    """导入 JSON 数据包"""
    mode = request.args.get("mode", "merge")  # replace 或 merge
    file = request.files.get("file")
    if not file:
        # 也支持 JSON body
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "请上传 JSON 文件"}), 400
    else:
        data = json.loads(file.read().decode("utf-8"))

    if "tables" not in data:
        return jsonify({"error": "无效的数据格式"}), 400

    db = get_db()

    if mode == "replace":
        # 清空所有表
        for table in TABLES:
            db.execute(f"DELETE FROM {table}")
        # 重置自增 ID
        for table in TABLES:
            db.execute(f"DELETE FROM sqlite_sequence WHERE name='{table}'")

    imported = {}
    for table, rows in data["tables"].items():
        if table not in TABLES:
            continue
        info = TABLES[table]
        count = 0
        for row in rows:
            if mode == "replace":
                # 直接插入（保留原始 id）
                cols = ["id"] + info["cols"]
                vals = []
                for c in cols:
                    v = row.get(c, "" if c != "id" else None)
                    if v is None and c == "id":
                        continue
                    vals.append(v)
                if "id" in row and row["id"]:
                    placeholders = ", ".join(["?"] * len(vals))
                    try:
                        db.execute(
                            f"INSERT INTO {table} ({', '.join(cols[:len(vals)])}) VALUES ({placeholders})",
                            vals,
                        )
                        count += 1
                    except sqlite3.IntegrityError:
                        pass
            else:  # merge
                if "id" in row and row["id"]:
                    # 检查是否存在
                    existing = db.execute(
                        f"SELECT id FROM {table} WHERE id = ?", (row["id"],)
                    ).fetchone()
                    if existing:
                        # 更新
                        sets = [f"{c} = ?" for c in info["cols"]]
                        vals = [row.get(c, "") for c in info["cols"]]
                        vals.append(row["id"])
                        db.execute(
                            f"UPDATE {table} SET {', '.join(sets)} WHERE id = ?", vals
                        )
                        count += 1
                    else:
                        # 插入（保留 id）
                        cols = ["id"] + info["cols"]
                        vals = [row["id"]] + [row.get(c, "") for c in info["cols"]]
                        placeholders = ", ".join(["?"] * len(vals))
                        try:
                            db.execute(
                                f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
                                vals,
                            )
                            count += 1
                        except sqlite3.IntegrityError:
                            pass
                else:
                    # 没有 id，直接插入
                    vals = {c: row.get(c, "") for c in info["cols"]}
                    cols_str = ", ".join(vals.keys())
                    placeholders = ", ".join(["?"] * len(vals))
                    try:
                        db.execute(
                            f"INSERT INTO {table} ({cols_str}) VALUES ({placeholders})",
                            list(vals.values()),
                        )
                        count += 1
                    except sqlite3.IntegrityError:
                        pass
        imported[table] = count

    db.commit()
    return jsonify({"message": f"导入完成（模式: {mode}）", "imported": imported})


# ──────────── 统计 API ────────────
@app.route("/api/stats/expense")
def stats_expense():
    """支出分类汇总，支持 ?month=2026-07"""
    month = request.args.get("month", datetime.now().strftime("%Y-%m"))
    db = get_db()
    rows = db.execute(
        """SELECT category, SUM(amount) as total, COUNT(*) as count
           FROM expense
           WHERE date LIKE ?
           GROUP BY category
           ORDER BY total DESC""",
        (f"{month}%",),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/stats/income")
def stats_income():
    """收入汇总"""
    month = request.args.get("month", datetime.now().strftime("%Y-%m"))
    db = get_db()
    rows = db.execute(
        """SELECT category, SUM(amount) as total, COUNT(*) as count
           FROM income
           WHERE date LIKE ?
           GROUP BY category
           ORDER BY total DESC""",
        (f"{month}%",),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/stats/trend")
def stats_trend():
    """近 6 个月收支趋势（与云函数接口保持一致）"""
    now = datetime.now()
    months = []
    for i in range(5, -1, -1):
        y, m = now.year, now.month - i
        while m <= 0:
            m += 12
            y -= 1
        months.append(f"{y:04d}-{m:02d}")
    db = get_db()
    inc, exp = [], []
    for mo in months:
        inc.append(
            round(
                db.execute(
                    "SELECT COALESCE(SUM(amount),0) FROM income WHERE date LIKE ?",
                    (mo + "%",),
                ).fetchone()[0],
                2,
            )
        )
        exp.append(
            round(
                db.execute(
                    "SELECT COALESCE(SUM(amount),0) FROM expense WHERE date LIKE ?",
                    (mo + "%",),
                ).fetchone()[0],
                2,
            )
        )
    return jsonify({"months": months, "inc": inc, "exp": exp})


@app.route("/api/stats/overview")
def stats_overview():
    """首页概览：本月收支总览（含大额拆分，与云函数一致）"""
    month = request.args.get("month", datetime.now().strftime("%Y-%m"))
    db = get_db()

    total_income = db.execute(
        "SELECT COALESCE(SUM(amount),0) FROM income WHERE date LIKE ?",
        (f"{month}%",),
    ).fetchone()[0]

    large_income = db.execute(
        "SELECT COALESCE(SUM(amount),0) FROM income WHERE date LIKE ? AND large = 1",
        (f"{month}%",),
    ).fetchone()[0]

    total_expense = db.execute(
        "SELECT COALESCE(SUM(amount),0) FROM expense WHERE date LIKE ? AND (large IS NULL OR large != 1)",
        (f"{month}%",),
    ).fetchone()[0]

    large_expense = db.execute(
        "SELECT COALESCE(SUM(amount),0) FROM expense WHERE date LIKE ? AND large = 1",
        (f"{month}%",),
    ).fetchone()[0]

    latest_finance = db.execute(
        "SELECT * FROM finance_snapshot ORDER BY date DESC LIMIT 1"
    ).fetchone()

    latest_electricity = db.execute(
        "SELECT * FROM electricity ORDER BY date DESC LIMIT 1"
    ).fetchone()

    return jsonify(
        {
            "month": month,
            "total_income": round(total_income, 2),
            "large_income": round(large_income, 2),
            "total_expense": round(total_expense, 2),
            "large_expense": round(large_expense, 2),
            "balance": round(total_income - total_expense - large_expense, 2),
            "latest_finance": dict(latest_finance) if latest_finance else None,
            "latest_electricity": dict(latest_electricity) if latest_electricity else None,
        }
    )


# ──────────── 前端页面 ────────────
@app.route("/")
def index():
    # 每次请求重新读取文件，修改 HTML 后无需重启服务器
    if os.path.exists(_html_path):
        with open(_html_path, "r", encoding="utf-8") as _f:
            return _f.read()
    return INDEX_HTML


# ──────────── 启动 ────────────
if __name__ == "__main__":
    init_db()
    print("=" * 50)
    print("  日知录记账系统")
    print("  打开浏览器访问: http://localhost:5000")
    print("  按 Ctrl+C 退出")
    print("=" * 50)
    app.run(host="127.0.0.1", port=5000, debug=False)
