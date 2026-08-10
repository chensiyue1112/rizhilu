"""
一次性迁移脚本：将 Obsidian vault 中的现有数据导入 SQLite
运行: python 迁移数据.py
"""
import sqlite3
import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VAULT_DIR = os.path.join(os.path.dirname(BASE_DIR), "日知录")  # Obsidian vault
DB_PATH = os.path.join(BASE_DIR, "data.db")

db = sqlite3.connect(DB_PATH)
db.execute("PRAGMA journal_mode=WAL")


def migrate_personal_finance():
    """解析 记/个人财务.md 表格"""
    path = os.path.join(VAULT_DIR, "记", "个人财务.md")
    if not os.path.exists(path):
        print("WARNING 个人财务.md 不存在，跳过")
        return

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # 匹配表格行: | 2026.5.29 | 53,905 | 27100 | **81005** | ...
    pattern = r"\|\s*([\d.]+)\s*\|\s*([\d,]+)\s*\|\s*([\d,]+)\s*\|\s*\*{0,2}[\d,]+\*{0,2}\s*\|\s*([\d,]+)\s*\|\s*([\d,]+)\s*\|\s*([\d,]+)"
    rows = re.findall(pattern, content)

    for row in rows:
        date_str = row[0].strip()
        # 转换日期: 2026.5.29 → 2026-05-29
        parts = date_str.split(".")
        if len(parts) == 3:
            date_str = f"{parts[0]}-{parts[1].zfill(2)}-{parts[2].zfill(2)}"

        liquid = float(row[1].replace(",", ""))
        debt = float(row[2].replace(",", ""))
        stock = float(row[3].replace(",", ""))
        fund = float(row[4].replace(",", ""))
        bond = float(row[5].replace(",", ""))

        # 检查是否已存在
        existing = db.execute(
            "SELECT id FROM finance_snapshot WHERE date = ?", (date_str,)
        ).fetchone()
        if existing:
            print(f"  跳过已存在的财务快照: {date_str}")
            continue

        db.execute(
            "INSERT INTO finance_snapshot (date, liquid_assets, debt, stock, fund, convertible_bond) VALUES (?,?,?,?,?,?)",
            (date_str, liquid, debt, stock, fund, bond),
        )
        print(f"  [OK] 财务快照: {date_str}")

    db.commit()


def migrate_income():
    """解析 记/收入情况.md"""
    path = os.path.join(VAULT_DIR, "记", "收入情况.md")
    if not os.path.exists(path):
        print("WARNING 收入情况.md 不存在，跳过")
        return

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # 匹配每一行: 6.30 10 10 立减金5 生活费2000（ 不纳入）
    # 数字可能是收入金额
    for line in content.split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("|") or line.startswith("---") or "[[" in line:
            continue

        # 提取日期前缀
        m = re.match(r"(\d+\.\d+)\s+(.+)", line)
        if not m:
            continue

        date_part = m.group(1)
        rest = m.group(2)

        # 转换日期: 6.30 → 2026-06-30
        parts = date_part.split(".")
        if len(parts) == 2:
            year = 2026 if int(parts[0]) >= 1 else 2025  # 假设 2026 年
            date_str = f"{year}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"

        # 解析数字和分类
        # 格式: 数字 数字 立减金数字 生活费数字（不纳入）...
        tokens = re.findall(r"(\d+(?:\.\d+)?)|(立减金\d+(?:\.\d+)?)|(生活费\d+(?:\.\d+)?)", rest)

        for t in tokens:
            num, lijian, shenghuo = t
            if num:
                amount = float(num)
                # 检查是否已存在（按日期+金额近似匹配）
                existing = db.execute(
                    "SELECT id FROM income WHERE date = ? AND amount = ?",
                    (date_str, amount),
                ).fetchone()
                if existing:
                    continue
                db.execute(
                    "INSERT INTO income (date, amount, category) VALUES (?,?,?)",
                    (date_str, amount, "日常收入"),
                )
            elif lijian:
                amount = float(lijian.replace("立减金", ""))
                existing = db.execute(
                    "SELECT id FROM income WHERE date = ? AND amount = ?",
                    (date_str, amount),
                ).fetchone()
                if existing:
                    continue
                db.execute(
                    "INSERT INTO income (date, amount, category) VALUES (?,?,?)",
                    (date_str, amount, "立减金"),
                )
            elif shenghuo:
                # 生活费不纳入（按用户备注）
                pass

    db.commit()
    print("  [OK] 收入数据迁移完成")


def migrate_expense():
    """解析 记/花费记录.md"""
    path = os.path.join(VAULT_DIR, "记", "花费记录.md")
    if not os.path.exists(path):
        print("WARNING 花费记录.md 不存在，跳过")
        return

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    CATEGORY_MAP = {
        "午饭": "餐饮", "晚饭": "餐饮", "晚餐": "餐饮", "早餐": "餐饮", "午餐": "餐饮",
        "面包": "餐饮", "饼干": "餐饮", "麦当劳": "餐饮", "麦当劳团购券": "餐饮", "自助": "餐饮",
        "打车": "交通", "地铁": "交通", "骑车": "交通", "单车": "交通", "共享电动车": "交通", "打车券": "交通",
        "话费": "通讯",
        "房租": "房租",
        "鱼缸": "鱼缸", "宠物鱼": "鱼缸", "鱼缸膜": "鱼缸", "鱼缸底板": "鱼缸", "鱼缸过滤": "鱼缸",
        "deepseekapi": "工具",
        "手机膜": "购物", "剪刀": "购物", "雨衣": "购物", "头盔": "购物", "研钵": "购物", "锁头": "购物",
        "捐款": "捐款",
        "电动车": "电动车",
        "外卖药品": "医疗", "中药调理茶饮": "医疗", "胃药": "医疗",
        "红牛咖啡": "餐饮",
        "飞机账号": "工具",
    }

    for line in content.split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("|") or line.startswith("---") or "[[" in line:
            continue

        m = re.match(r"(\d+\.\d+)\s+(.+)", line)
        if not m:
            continue

        date_part = m.group(1)
        rest = m.group(2)

        parts = date_part.split(".")
        if len(parts) == 2:
            year = 2026 if int(parts[0]) >= 1 else 2025
            date_str = f"{year}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"

        # 解析每一项: 打车12 早餐4 打车券8.8
        items = re.findall(r"(\D+?)(\d+(?:\.\d+)?)", rest)

        for desc, amount_str in items:
            desc = desc.strip()
            amount = float(amount_str)

            # 映射分类
            category = "其他"
            for keyword, cat in CATEGORY_MAP.items():
                if keyword in desc:
                    category = cat
                    break

            # 去重检查
            existing = db.execute(
                "SELECT id FROM expense WHERE date = ? AND amount = ? AND description = ?",
                (date_str, amount, desc),
            ).fetchone()
            if existing:
                continue

            db.execute(
                "INSERT INTO expense (date, amount, category, description) VALUES (?,?,?,?)",
                (date_str, amount, category, desc),
            )

    db.commit()
    print("  [OK] 支出数据迁移完成")


def migrate_electricity():
    """解析 记/量化数据/用电情况.md"""
    path = os.path.join(VAULT_DIR, "记", "量化数据", "用电情况.md")
    if not os.path.exists(path):
        print("WARNING 用电情况.md 不存在，跳过")
        return

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # 匹配: | 7.16晚 | 9394.1 |
    pattern = r"\|\s*([\d.]+)[早晚]?\s*\|\s*([\d.]+)\s*\|"
    for m in re.finditer(pattern, content):
        date_part = m.group(1)
        reading = float(m.group(2))

        parts = date_part.split(".")
        if len(parts) >= 2:
            year = 2026 if int(parts[0]) >= 1 else 2025
            date_str = f"{year}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"

            existing = db.execute(
                "SELECT id FROM electricity WHERE date = ? AND reading = ?",
                (date_str, reading),
            ).fetchone()
            if existing:
                continue

            db.execute(
                "INSERT INTO electricity (date, reading) VALUES (?,?)",
                (date_str, reading),
            )

    db.commit()
    print("  [OK] 用电数据迁移完成")


def migrate_diary():
    """解析 记/日记/*.md"""
    diary_dir = os.path.join(VAULT_DIR, "记", "日记")
    if not os.path.exists(diary_dir):
        print("WARNING 日记目录不存在，跳过")
        return

    for fname in os.listdir(diary_dir):
        if not fname.endswith(".md"):
            continue
        # 跳过日记索引文件
        if "日记" in fname and any(c in fname for c in "📝"):
            continue

        path = os.path.join(diary_dir, fname)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # 去掉文件名后缀
        title = fname.replace(".md", "")

        # 跳过纯索引文件（无日期信息）
        if not re.search(r"\d{4}年", title):
            continue

        # 提取日期
        date_match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", title)
        if date_match:
            date_str = f"{date_match.group(1)}-{date_match.group(2).zfill(2)}-{date_match.group(3).zfill(2)}"
        elif "月" in title:
            # 如 "2025年12月"
            ym = re.search(r"(\d{4})年(\d{1,2})月", title)
            if ym:
                date_str = f"{ym.group(1)}-{ym.group(2).zfill(2)}-01"
            else:
                date_str = "2025-01-01"
        else:
            date_str = "2025-01-01"

        # 检查重复
        existing = db.execute(
            "SELECT id FROM diary WHERE title = ? AND date = ?",
            (title, date_str),
        ).fetchone()
        if existing:
            try: print(f"  跳过已存在的日记: {title}")
            except: print(f"  跳过已存在的日记 (id={existing[0]})")
            continue

        db.execute(
            "INSERT INTO diary (date, title, content) VALUES (?,?,?)",
            (date_str, title, content.strip()),
        )
        try: print(f"  [OK] 日记: {title}")
        except: print(f"  [OK] 日记 已导入 (date={date_str})")

    db.commit()


if __name__ == "__main__":
    print("=" * 50)
    print("  数据迁移：Obsidian → SQLite")
    print("=" * 50)

    print("\n[财务] 迁移财务快照...")
    migrate_personal_finance()

    print("\n[收入] 迁移收入数据...")
    migrate_income()

    print("\n[支出] 迁移支出数据...")
    migrate_expense()

    print("\n[用电] 迁移用电数据...")
    migrate_electricity()

    print("\n[日记] 迁移日记...")
    migrate_diary()

    db.close()
    print("\n[完成] 迁移完成！")
