import streamlit as st
import sqlite3
import pandas as pd
import json
import os
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path
from io import BytesIO

# --- CẤU HÌNH TRANG WEB ---
st.set_page_config(
    page_title="Tennis Vui - Quản lý Tài chính",
    page_icon="🎾",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- KHỞI TẠO CÁC HẰNG SỐ ---
APP_TITLE = "Tennis vui"
DEFAULT_DB_FILE = "tennis_monthly.tennis"

DEFAULT_RULES = {
    "fine_lose": 20000,
    "fine_draw": 20000,
    "fine_lose_zero": 40000,
    "point_win": 3,
    "point_draw": 1,
    "point_loss": 0,
}

EXPENSE_TYPES = ["Thuê sân", "Mua bóng", "Lượm banh", "Ăn uống", "Tiệc cuối tháng", "Chi phí khác"]
WEIGHTED_EXPENSE_TYPES = {"Thuê sân", "Mua bóng", "Lượm banh"}
BALL_PICKER_EXPENSE_TYPES = {"Mua bóng", "Lượm banh"}
FUND_ONLY_EXPENSE_TYPES = {"Tiệc cuối tháng"}
ALL_MEMBER_DEFAULT_EXPENSE_TYPES = WEIGHTED_EXPENSE_TYPES.union(FUND_ONLY_EXPENSE_TYPES)
KNOWN_EXPENSE_TYPES = WEIGHTED_EXPENSE_TYPES.union({"Ăn uống"}, FUND_ONLY_EXPENSE_TYPES)

DEFAULT_INCOME_TYPES = ["Thu quỹ", "Thu vãng lai", "Thu khác"]
AUTO_ROUNDED_MEMBER_INCOME = "Thu hội viên làm tròn"
AUTO_EXTERNAL_INCOME = "Thu người ngoài"
AUTO_INCOME_TYPES = {AUTO_ROUNDED_MEMBER_INCOME, AUTO_EXTERNAL_INCOME}
FUND_ENTITY_NAME = "Quỹ"
LEGACY_FUND_ENTITY_NAMES = {FUND_ENTITY_NAME, "Chủ sân"}

# --- CÁC HÀM HỖ TRỢ TỪ TENNIS VUI ---
def normalize_text_key(value):
    text = str(value or "").strip().lower().replace("đ", "d")
    text = "".join(
        char for char in unicodedata.normalize("NFD", text)
        if not unicodedata.combining(char)
    )
    for old, new in ((" ", "_"), ("-", "_"), ("/", "_"), (".", "")):
        text = text.replace(old, new)
    while "__" in text:
        text = text.replace("__", "_")
    return text.strip("_")


def load_json_list(value):
    try:
        data = json.loads(value or "[]")
    except Exception:
        return []
    return data if isinstance(data, list) else []


def includes_all_or_empty(value):
    participants = load_json_list(value)
    return "All" in participants or not participants


def previous_month(month):
    try:
        y, m = [int(x) for x in month.split("-")]
        if m == 1:
            return f"{y-1}-12"
        return f"{y}-{m-1:02d}"
    except Exception:
        return datetime.now().strftime("%Y-%m")


# --- PHẦN XỬ LÝ DATABASE ---
class TennisDB:
    def __init__(self, db_path):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.create_tables()

    def create_tables(self):
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                active INTEGER NOT NULL DEFAULT 1,
                weekly_sessions INTEGER NOT NULL DEFAULT 3
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_date TEXT NOT NULL,
                match_no INTEGER NOT NULL DEFAULT 1,
                team_a1 TEXT NOT NULL,
                team_a2 TEXT NOT NULL,
                team_b1 TEXT NOT NULL,
                team_b2 TEXT NOT NULL,
                score_a INTEGER NOT NULL,
                score_b INTEGER NOT NULL,
                match_money INTEGER,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                match_bet INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS monthly_finance (
                month TEXT PRIMARY KEY,
                court_cost INTEGER NOT NULL DEFAULT 0,
                ball_cost INTEGER NOT NULL DEFAULT 0,
                picker_cost INTEGER NOT NULL DEFAULT 0,
                reserve_fund INTEGER NOT NULL DEFAULT 1800000,
                fund_balance INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS finance_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                month TEXT NOT NULL,
                name TEXT NOT NULL,
                member_type INTEGER NOT NULL DEFAULT 3
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS finance_expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                month TEXT NOT NULL,
                expense_date TEXT NOT NULL,
                expense_type TEXT NOT NULL,
                amount INTEGER NOT NULL DEFAULT 0,
                participants TEXT NOT NULL DEFAULT '[]',
                payer TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT ''
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS finance_incomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                month TEXT NOT NULL,
                income_date TEXT NOT NULL,
                income_type TEXT NOT NULL DEFAULT 'Thu khác',
                amount INTEGER NOT NULL DEFAULT 0,
                collector TEXT NOT NULL DEFAULT 'Quỹ',
                note TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self.conn.commit()
        self.ensure_schema_columns()
        self.ensure_default_rules()

    def table_columns(self, table_name):
        try:
            cur = self.conn.cursor()
            return [col[1] for col in cur.execute(f"PRAGMA table_info({table_name})").fetchall()]
        except Exception:
            return []

    def ensure_column(self, table_name, column_name, alter_sql):
        if column_name not in self.table_columns(table_name):
            self.conn.execute(alter_sql)

    def ensure_schema_columns(self):
        migrations = [
            ("matches", "match_no", "ALTER TABLE matches ADD COLUMN match_no INTEGER NOT NULL DEFAULT 1"),
            ("matches", "match_money", "ALTER TABLE matches ADD COLUMN match_money INTEGER"),
            ("matches", "match_bet", "ALTER TABLE matches ADD COLUMN match_bet INTEGER NOT NULL DEFAULT 0"),
            ("matches", "note", "ALTER TABLE matches ADD COLUMN note TEXT NOT NULL DEFAULT ''"),
            ("players", "weekly_sessions", "ALTER TABLE players ADD COLUMN weekly_sessions INTEGER NOT NULL DEFAULT 3"),
            ("monthly_finance", "fund_balance", "ALTER TABLE monthly_finance ADD COLUMN fund_balance INTEGER NOT NULL DEFAULT 0"),
            ("finance_expenses", "payer", "ALTER TABLE finance_expenses ADD COLUMN payer TEXT NOT NULL DEFAULT ''"),
            ("finance_expenses", "note", "ALTER TABLE finance_expenses ADD COLUMN note TEXT NOT NULL DEFAULT ''"),
            ("finance_incomes", "collector", "ALTER TABLE finance_incomes ADD COLUMN collector TEXT NOT NULL DEFAULT 'Quỹ'"),
            ("finance_incomes", "note", "ALTER TABLE finance_incomes ADD COLUMN note TEXT NOT NULL DEFAULT ''"),
        ]
        for table_name, column_name, alter_sql in migrations:
            self.ensure_column(table_name, column_name, alter_sql)
        self.conn.commit()

    def ensure_default_rules(self):
        cur = self.conn.cursor()
        for k, v in DEFAULT_RULES.items():
            cur.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, str(v)))
        self.conn.commit()

    def get_rules(self):
        cur = self.conn.cursor()
        rows = dict(cur.execute("SELECT key, value FROM settings").fetchall())
        result = {}
        for k, v in DEFAULT_RULES.items():
            try:
                result[k] = int(rows.get(k, v))
            except ValueError:
                result[k] = v
        return result

    def save_rules(self, rules_dict):
        cur = self.conn.cursor()
        for k, v in rules_dict.items():
            cur.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (k, str(v)))
        self.conn.commit()

    def get_player_names(self):
        cur = self.conn.cursor()
        return [r[0] for r in cur.execute("SELECT name FROM players ORDER BY name").fetchall()]

    def get_finance_actor_names(self):
        names = [FUND_ENTITY_NAME]
        fund_key = normalize_text_key(FUND_ENTITY_NAME)
        for name in self.get_player_names():
            if normalize_text_key(name) != fund_key:
                names.append(name)
        return names

    def get_fund_actor_keys(self):
        return {normalize_text_key(name) for name in LEGACY_FUND_ENTITY_NAMES}

    def get_auto_income_collector(self, income_type, month):
        if income_type in AUTO_INCOME_TYPES:
            return FUND_ENTITY_NAME
        key = f"auto_income_collector::{month}::{income_type}"
        try:
            row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            value = row[0].strip() if row and row[0] else ""
        except Exception:
            value = ""

        if value in self.get_finance_actor_names():
            return value
        return FUND_ENTITY_NAME

    def get_previous_month_fund(self, month):
        prev = previous_month(month)
        cur = self.conn.cursor()
        row = cur.execute("SELECT fund_balance FROM monthly_finance WHERE month = ?", (prev,)).fetchone()
        if row and row[0] is not None:
            return int(row[0])
        return 0

    def reset_sqlite_sequence(self, table_name, count):
        try:
            self.conn.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = ?", (count, table_name))
        except Exception:
            pass

    def reorder_finance_members_silent(self, month):
        cur = self.conn.cursor()
        rows = cur.execute("""
            SELECT month, name, member_type
            FROM finance_members
            ORDER BY month, LOWER(name), id
        """).fetchall()
        try:
            cur.execute("DELETE FROM finance_members")
            cur.executemany(
                "INSERT INTO finance_members(id, month, name, member_type) VALUES (?, ?, ?, ?)",
                [(idx, m, name, member_type) for idx, (m, name, member_type) in enumerate(rows, start=1)]
            )
            self.reset_sqlite_sequence("finance_members", len(rows))
            self.conn.commit()
        except Exception:
            self.conn.rollback()

    def reorder_finance_expenses_silent(self, month):
        cur = self.conn.cursor()
        rows = cur.execute("""
            SELECT expense_date, expense_type, amount, participants, payer, note
            FROM finance_expenses
            WHERE month = ?
            ORDER BY expense_date, id
        """, (month,)).fetchall()
        try:
            cur.execute("DELETE FROM finance_expenses WHERE month = ?", (month,))
            cur.executemany(
                """
                INSERT INTO finance_expenses(id, month, expense_date, expense_type, amount, participants, payer, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [(idx, month, *row) for idx, row in enumerate(rows, start=1)]
            )
            self.reset_sqlite_sequence("finance_expenses", len(rows))
            self.conn.commit()
        except Exception:
            self.conn.rollback()

    def reorder_finance_incomes_silent(self, month):
        cur = self.conn.cursor()
        rows = cur.execute("""
            SELECT income_date, income_type, amount, collector, note
            FROM finance_incomes
            WHERE month = ?
            ORDER BY income_date, id
        """, (month,)).fetchall()
        try:
            cur.execute("DELETE FROM finance_incomes WHERE month = ?", (month,))
            cur.executemany(
                """
                INSERT INTO finance_incomes(id, month, income_date, income_type, amount, collector, note)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [(idx, month, *row) for idx, row in enumerate(rows, start=1)]
            )
            self.reset_sqlite_sequence("finance_incomes", len(rows))
            self.conn.commit()
        except Exception:
            self.conn.rollback()

    def fetch_matches(self, month=None):
        cur = self.conn.cursor()
        if month:
            return cur.execute(
                """
                SELECT id, match_date, match_no, team_a1, team_a2, team_b1, team_b2, score_a, score_b, match_money, match_bet, note
                FROM matches
                WHERE substr(match_date, 1, 7) = ?
                ORDER BY match_date DESC, match_no DESC, id DESC
                """,
                (month,)
            ).fetchall()
        else:
            return cur.execute(
                """
                SELECT id, match_date, match_no, team_a1, team_a2, team_b1, team_b2, score_a, score_b, match_money, match_bet, note
                FROM matches
                ORDER BY match_date DESC, match_no DESC, id DESC
                """
            ).fetchall()

    def get_month_match_money_detail_map(self, month):
        cur = self.conn.cursor()
        rows = cur.execute(
            """
            SELECT id, match_date, match_no, team_a1, team_a2, team_b1, team_b2,
                   score_a, score_b, match_money, match_bet
            FROM matches
            WHERE substr(match_date, 1, 7) = ?
            ORDER BY match_date, match_no, id
            """,
            (month,)
        ).fetchall()

        rules = self.get_rules()
        money_map = {}

        def get_row(name):
            name = (name or "").strip()
            if not name:
                return None
            if name not in money_map:
                money_map[name] = {"match_money": 0, "bet_money": 0, "total": 0}
            return money_map[name]

        def add_money(name, match_part, bet_part):
            row = get_row(name)
            if row is None:
                return
            row["match_money"] += int(match_part or 0)
            row["bet_money"] += int(bet_part or 0)
            row["total"] += int(match_part or 0) + int(bet_part or 0)

        for _mid, _date, _no, a1, a2, b1, b2, sa, sb, match_money, match_bet in rows:
            team_a = [x for x in [a1, a2] if x]
            team_b = [x for x in [b1, b2] if x]

            base = match_money if match_money is not None else rules["fine_lose"]
            draw = match_money if match_money is not None else rules["fine_draw"]
            zero = match_money if match_money is not None else rules["fine_lose_zero"]
            bet = int(match_bet or 0)

            if sa > sb:
                for name in team_b:
                    add_money(name, zero if sb == 0 else base, bet)
            elif sb > sa:
                for name in team_a:
                    add_money(name, zero if sa == 0 else base, bet)
            else:
                for name in team_a + team_b:
                    add_money(name, draw, bet)

        return money_map

    def calculate_stats(self, month=None):
        rows = self.fetch_matches(month)
        rules = self.get_rules()
        stats = {}

        def get_player(name):
            if name not in stats:
                stats[name] = {
                    "name": name,
                    "matches": 0,
                    "wins": 0,
                    "draws": 0,
                    "losses": 0,
                    "gf": 0,
                    "ga": 0,
                    "match_money": 0,
                    "bet_money": 0,
                    "money": 0,
                }
            return stats[name]

        for _, _, _, a1, a2, b1, b2, sa, sb, match_money, match_bet, _note in rows:
            team_a = [name for name in [a1, a2] if name]
            team_b = [name for name in [b1, b2] if name]
            for name in team_a + team_b:
                get_player(name)["matches"] += 1
            for name in team_a:
                p = get_player(name)
                p["gf"] += sa
                p["ga"] += sb
            for name in team_b:
                p = get_player(name)
                p["gf"] += sb
                p["ga"] += sa

            lose_match_money = match_money if match_money is not None else rules["fine_lose"]
            draw_match_money = match_money if match_money is not None else rules["fine_draw"]
            zero_match_money = match_money if match_money is not None else rules["fine_lose_zero"]
            bet_money = int(match_bet or 0)

            def add_money(player_name, match_part):
                p = get_player(player_name)
                p["match_money"] += int(match_part or 0)
                p["bet_money"] += bet_money
                p["money"] += int(match_part or 0) + bet_money

            if sa > sb:
                for name in team_a:
                    get_player(name)["wins"] += 1
                for name in team_b:
                    p = get_player(name)
                    p["losses"] += 1
                    add_money(name, zero_match_money if sb == 0 else lose_match_money)
            elif sb > sa:
                for name in team_b:
                    get_player(name)["wins"] += 1
                for name in team_a:
                    p = get_player(name)
                    p["losses"] += 1
                    add_money(name, zero_match_money if sa == 0 else lose_match_money)
            else:
                for name in team_a + team_b:
                    p = get_player(name)
                    p["draws"] += 1
                    add_money(name, draw_match_money)
        return list(stats.values())

    def calculate_finance_rows(self, month):
        cur = self.conn.cursor()
        
        # Ensure initial record for the month
        cur.execute(
            "INSERT OR IGNORE INTO monthly_finance(month, court_cost, ball_cost, picker_cost, reserve_fund, fund_balance) VALUES (?, 0, 0, 0, 1800000, 0)",
            (month,)
        )

        players = cur.execute("SELECT name, weekly_sessions FROM players ORDER BY name").fetchall()
        existing = {
            r[0].lower(): r[1]
            for r in cur.execute("SELECT name, id FROM finance_members WHERE month = ?", (month,)).fetchall()
        }

        for name, weekly in players:
            weekly = int(weekly or 3)
            if weekly not in [1, 2, 3]:
                weekly = 3
            if name.lower() in existing:
                cur.execute("UPDATE finance_members SET member_type = ? WHERE id = ?", (weekly, existing[name.lower()]))
            else:
                cur.execute("INSERT INTO finance_members(month, name, member_type) VALUES (?, ?, ?)", (month, name, weekly))
        self.conn.commit()

        self.reorder_finance_expenses_silent(month)
        self.reorder_finance_incomes_silent(month)
        self.reorder_finance_members_silent(month)

        expenses = cur.execute(
            """
            SELECT id, expense_date, expense_type, amount, participants, payer, note
            FROM finance_expenses
            WHERE month = ?
            ORDER BY id
            """,
            (month,)
        ).fetchall()

        incomes = cur.execute(
            """
            SELECT id, income_date, income_type, amount, collector, note
            FROM finance_incomes
            WHERE month = ?
            ORDER BY id
            """,
            (month,)
        ).fetchall()

        members = cur.execute(
            "SELECT id, name, member_type FROM finance_members WHERE month = ? ORDER BY id",
            (month,)
        ).fetchall()

        weekly_map = {
            str(name).strip().lower(): int(weekly or 3)
            for name, weekly in cur.execute("SELECT name, weekly_sessions FROM players").fetchall()
        }

        member_weights = []
        for member_id, name, member_type in members:
            weight = weekly_map.get(str(name).strip().lower(), int(member_type or 3))
            if weight not in [1, 2, 3]:
                weight = 3
            member_weights.append((member_id, name, weight))

        total_weight = sum(int(m[2]) for m in member_weights) or 1
        member_names = [m[1] for m in member_weights]
        member_name_set = {normalize_text_key(n) for n in member_names}
        
        rounded_fund_collector = self.get_auto_income_collector(AUTO_ROUNDED_MEMBER_INCOME, month)
        fund_collector = rounded_fund_collector
        fund_actor_keys = self.get_fund_actor_keys()
        fund_collector_key = normalize_text_key(fund_collector)
        if fund_collector_key:
            fund_actor_keys.add(fund_collector_key)

        # Paid by members
        paid_by_member_map = {name: 0 for name in member_names}
        for _eid, _date, _etype, amount, _participants, payer, _note in expenses:
            payer_key = normalize_text_key(payer)
            if payer_key and payer_key in fund_actor_keys:
                continue
            for member_name in member_names:
                if payer_key == normalize_text_key(member_name):
                    paid_by_member_map[member_name] += int(amount or 0)
                    break

        # Collected by members
        collected_by_member_map = {name: 0 for name in member_names}
        for income_row in incomes:
            collector = str(income_row[4] if len(income_row) > 4 else "").strip()
            amount = int(round(income_row[3] or 0))
            collector_key = normalize_text_key(collector)
            if collector_key and collector_key in fund_actor_keys:
                continue
            for member_name in member_names:
                if collector_key == normalize_text_key(member_name):
                    collected_by_member_map[member_name] += amount
                    break

        # External payments/collections
        paid_by_external_map = {}
        for _eid, _date, _etype, amount, _participants, payer, _note in expenses:
            payer_name = str(payer or "").strip()
            payer_key = normalize_text_key(payer_name)
            if payer_name and payer_key not in fund_actor_keys and payer_key not in member_name_set:
                paid_by_external_map[payer_name] = paid_by_external_map.get(payer_name, 0) + int(amount or 0)

        collected_by_external_map = {}
        for income_row in incomes:
            collector = str((income_row[4] if len(income_row) > 4 else "") or "").strip()
            collector_key = normalize_text_key(collector)
            if collector and collector_key not in fund_actor_keys and collector_key not in member_name_set:
                collected_by_external_map[collector] = collected_by_external_map.get(collector, 0) + int(income_row[3] or 0)

        court_total = sum(int(x[3] or 0) for x in expenses if x[2] == "Thuê sân")
        ball_total = sum(int(x[3] or 0) for x in expenses if x[2] == "Mua bóng")
        picker_total = sum(int(x[3] or 0) for x in expenses if x[2] == "Lượm banh")
        meal_total = sum(int(x[3] or 0) for x in expenses if x[2] == "Ăn uống")
        party_total = sum(int(x[3] or 0) for x in expenses if x[2] in FUND_ONLY_EXPENSE_TYPES)
        other_total = sum(int(x[3] or 0) for x in expenses if x[2] not in KNOWN_EXPENSE_TYPES)
        total_expense = court_total + ball_total + picker_total + meal_total + party_total + other_total
        other_income_total = sum(int(x[3] or 0) for x in incomes)

        extra_share_map = {}
        meal_share_map = {}

        def add_equal_share(target_map, amount, participants):
            if "All" in participants:
                extras = [p for p in participants if p and p != "All"]
                participants = member_names + extras
            participants = [p for p in participants if p and p != "All"]
            if not participants:
                return
            share = int(amount or 0) / len(participants)
            for name in participants:
                target_map[name] = target_map.get(name, 0) + share

        for _, _, expense_type, amount, participants_json, payer, _note in expenses:
            participants = load_json_list(participants_json)

            if expense_type in WEIGHTED_EXPENSE_TYPES:
                if "All" not in participants and participants:
                    add_equal_share(extra_share_map, amount, participants)
                continue

            if expense_type in FUND_ONLY_EXPENSE_TYPES:
                continue

            if expense_type == "Ăn uống":
                add_equal_share(meal_share_map, amount, participants)
            else:
                add_equal_share(extra_share_map, amount, participants)

        # Match fee detail
        all_match_detail_map = self.get_month_match_money_detail_map(month)
        player_name_set = {normalize_text_key(n) for n, _weekly in players}
        match_detail_map = {
            name: data
            for name, data in all_match_detail_map.items()
            if normalize_text_key(name) in player_name_set
        }
        match_money_map = {
            name: data.get("match_money", 0)
            for name, data in match_detail_map.items()
        }
        bet_money_map = {
            name: data.get("bet_money", 0)
            for name, data in match_detail_map.items()
        }
        match_revenue = sum(data.get("match_money", 0) for data in match_detail_map.values())
        bet_revenue = sum(data.get("bet_money", 0) for data in match_detail_map.values())
        prev_match_revenue = match_revenue + bet_revenue

        rows = []

        for member_id, name, weekly in member_weights:
            weight = int(weekly)

            court_share = sum(
                int(x[3] or 0) * weight / total_weight
                for x in expenses
                if x[2] == "Thuê sân" and includes_all_or_empty(x[4])
            )
            ball_picker_share = sum(
                int(x[3] or 0) * weight / total_weight
                for x in expenses
                if x[2] in BALL_PICKER_EXPENSE_TYPES and includes_all_or_empty(x[4])
            )

            meal_share = meal_share_map.get(name, 0)
            other_share = extra_share_map.get(name, 0)

            rows.append({
                "id": member_id,
                "name": name,
                "weekly": weekly,
                "court": court_share,
                "ball_picker": ball_picker_share,
                "meal": meal_share,
                "other_charge": other_share,
            })

        rows.sort(key=lambda r: str(r.get("name","")).lower())

        for r in rows:
            r["match_money"] = match_money_map.get(r["name"], 0)
            r["bet_money"] = bet_money_map.get(r["name"], 0)
            r["calc_fee"] = r["court"] + r["ball_picker"] + r["meal"] + r["other_charge"] + r["match_money"] + r["bet_money"]
            r["rounded_fee"] = int(((r["calc_fee"] + 9999) // 10000) * 10000)

            r["paid_by_member"] = paid_by_member_map.get(r["name"], 0)
            r["collected_by_member"] = collected_by_member_map.get(r["name"], 0)
            r["net_payable"] = r["rounded_fee"] - r["paid_by_member"] + r["collected_by_member"]

        external_names = set()
        for source_map in [meal_share_map, extra_share_map, paid_by_external_map, collected_by_external_map]:
            for name in source_map.keys():
                if str(name).strip() and str(name).strip().lower() not in member_name_set:
                    external_names.add(str(name).strip())

        external_rows = []
        for name in sorted(external_names, key=lambda x: x.lower()):
            meal_value = meal_share_map.get(name, 0)
            other_value = extra_share_map.get(name, 0)
            paid_value = paid_by_external_map.get(name, 0)
            collected_value = collected_by_external_map.get(name, 0)
            calc_value = meal_value + other_value
            net_value = calc_value - paid_value + collected_value
            external_rows.append({
                "name": name,
                "meal": meal_value,
                "other": other_value,
                "paid_by_external": paid_value,
                "collected_by_external": collected_value,
                "net_payable": net_value,
            })

        member_real_collect = sum(r["rounded_fee"] for r in rows)
        external_real_collect = sum(r["net_payable"] for r in external_rows)
        real_collect = member_real_collect + external_real_collect
        need_collect = sum(r["calc_fee"] for r in rows) + sum(r["meal"] + r["other"] for r in external_rows)
        round_extra = sum(r["rounded_fee"] - r["calc_fee"] for r in rows)
        total_rounded_fee = int(round(member_real_collect))
        external_income_total = int(round(external_real_collect))
        total_income_fund = other_income_total + total_rounded_fee + external_income_total
        fund_this_month = total_income_fund - total_expense

        external_fund_collector = self.get_auto_income_collector(AUTO_EXTERNAL_INCOME, month)

        external_fund_collector_key = normalize_text_key(external_fund_collector)
        if (
            external_fund_collector in collected_by_member_map
            and external_fund_collector_key not in fund_actor_keys
        ):
            collected_by_member_map[external_fund_collector] += external_income_total

        if (
            external_fund_collector
            and external_fund_collector_key not in fund_actor_keys
            and external_fund_collector_key not in member_name_set
        ):
            found_external = False
            for er in external_rows:
                if normalize_text_key(er.get("name", "")) == external_fund_collector_key:
                    er["collected_by_external"] = er.get("collected_by_external", 0) + external_income_total
                    er["net_payable"] = er.get("meal", 0) + er.get("other", 0)
                    found_external = True
                    break
            if not found_external and external_income_total:
                external_rows.append({
                    "name": external_fund_collector,
                    "meal": 0,
                    "other": 0,
                    "paid_by_external": 0,
                    "collected_by_external": external_income_total,
                    "net_payable": 0,
                })
            external_real_collect = sum(r.get("net_payable", 0) for r in external_rows)

        if (
            rounded_fund_collector in collected_by_member_map
            and normalize_text_key(rounded_fund_collector) not in fund_actor_keys
        ):
            collected_by_member_map[rounded_fund_collector] += total_rounded_fee

        for r in rows:
            r["collected_by_member"] = collected_by_member_map.get(r["name"], 0)
            r["net_payable"] = (
                r.get("rounded_fee", 0)
                - r.get("paid_by_member", 0)
                + r.get("collected_by_member", 0)
            )

        # Update fund balance in monthly_finance table
        prev_fund = self.get_previous_month_fund(month)
        fund_balance = prev_fund + fund_this_month
        cur.execute(
            "UPDATE monthly_finance SET court_cost=?, ball_cost=?, picker_cost=?, fund_balance=? WHERE month=?",
            (court_total, ball_total, picker_total, fund_balance, month)
        )
        self.conn.commit()

        summary = {
            "month": month,
            "prev_month": previous_month(month),
            "court_total": court_total,
            "ball_total": ball_total,
            "picker_total": picker_total,
            "meal_total": meal_total,
            "party_total": party_total,
            "other_total": other_total,
            "total_expense": total_expense,
            "prev_match_revenue": prev_match_revenue,
            "match_revenue": match_revenue,
            "bet_revenue": bet_revenue,
            "fund_collector": fund_collector,
            "rounded_fund_collector": rounded_fund_collector,
            "other_income_total": other_income_total,
            "total_rounded_fee": total_rounded_fee,
            "external_income_total": external_income_total,
            "total_income_fund": total_income_fund,
            "prev_fund": prev_fund,
            "need_collect": need_collect,
            "real_collect": real_collect,
            "member_real_collect": member_real_collect,
            "external_real_collect": external_real_collect,
            "external_rows": external_rows,
            "round_extra": round_extra,
            "fund_this_month": fund_this_month,
            "fund_balance": fund_balance,
        }
        return rows, expenses, incomes, summary


# --- GIAO DIỆN STREAMLIT ---
# Tùy chỉnh CSS giao diện
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&family=Inter:wght@300;400;500;600;700&display=swap');
    
    html, body, [data-testid="stAppViewContainer"] {
        font-family: 'Outfit', 'Inter', sans-serif;
    }
    
    .stTabs [data-baseweb="tab-list"] {
        gap: 10px;
    }
    
    .stTabs [data-baseweb="tab"] {
        background-color: #1e293b;
        border-radius: 8px;
        color: #94a3b8;
        font-weight: 600;
        padding: 10px 20px;
        border: 1px solid #334155;
    }
    
    .stTabs [data-baseweb="tab"]:hover {
        background-color: #334155;
        color: #f8fafc;
    }
    
    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, #0ea5e9 0%, #0284c7 100%) !important;
        color: #ffffff !important;
        border: none !important;
    }
    
    .metric-card {
        background: #1e293b;
        border: 1px solid #334155;
        border-radius: 12px;
        padding: 20px;
        box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1);
        text-align: center;
    }
    
    .metric-title {
        color: #94a3b8;
        font-size: 14px;
        font-weight: 500;
        margin-bottom: 5px;
    }
    
    .metric-value {
        font-size: 24px;
        font-weight: 700;
        margin-bottom: 5px;
    }
    
    .header-gradient {
        background: linear-gradient(90deg, #38bdf8 0%, #0284c7 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-weight: 800;
        font-size: 42px;
        margin-bottom: 10px;
    }
    </style>
    """,
    unsafe_allow_html=True
)

# --- QUẢN LÝ DANH SÁCH FILE DATABASE ---
st.sidebar.markdown("### 🎾 CƠ SỞ DỮ LIỆU")

# Tìm kiếm các file .tennis trong thư mục
local_files = [f for f in os.listdir(".") if f.endswith(".tennis")]
if DEFAULT_DB_FILE not in local_files and not os.path.exists(DEFAULT_DB_FILE):
    # Tạo db mặc định nếu chưa có
    conn = sqlite3.connect(DEFAULT_DB_FILE)
    conn.close()
    local_files.append(DEFAULT_DB_FILE)

# Cho phép người dùng upload file từ iPhone
uploaded_file = st.sidebar.file_uploader("Tải file .tennis từ máy lên", type=["tennis", "db"])
if uploaded_file is not None:
    save_path = Path(uploaded_file.name)
    with open(save_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    st.sidebar.success(f"Đã tải lên: {uploaded_file.name}")
    if uploaded_file.name not in local_files:
        local_files.append(uploaded_file.name)

# Chọn database hoạt động
selected_db = st.sidebar.selectbox(
    "Chọn file dữ liệu để làm việc:",
    options=sorted(local_files),
    index=sorted(local_files).index(DEFAULT_DB_FILE) if DEFAULT_DB_FILE in local_files else 0
)

# Khởi tạo db kết nối
db = TennisDB(selected_db)

# --- TRÍCH XUẤT DANH SÁCH THÁNG ---
def get_available_months():
    cur = db.conn.cursor()
    months = set()
    try:
        m_rows = cur.execute("SELECT DISTINCT substr(match_date, 1, 7) FROM matches").fetchall()
        for r in m_rows:
            if r[0]: months.add(r[0])
    except Exception: pass
    try:
        e_rows = cur.execute("SELECT DISTINCT month FROM finance_expenses").fetchall()
        for r in e_rows:
            if r[0]: months.add(r[0])
    except Exception: pass
    try:
        i_rows = cur.execute("SELECT DISTINCT month FROM finance_incomes").fetchall()
        for r in i_rows:
            if r[0]: months.add(r[0])
    except Exception: pass
    try:
        f_rows = cur.execute("SELECT DISTINCT month FROM monthly_finance").fetchall()
        for r in f_rows:
            if r[0]: months.add(r[0])
    except Exception: pass
    
    if not months:
        months.add(datetime.now().strftime("%Y-%m"))
    return sorted(list(months), reverse=True)

available_months = get_available_months()
selected_month = st.sidebar.selectbox("Chọn tháng làm việc:", options=available_months)

# Tiêu đề chính
st.markdown(f'<div class="header-gradient">TENNIS VUI</div>', unsafe_allow_html=True)
st.markdown(f"**Dữ liệu đang xem:** `{selected_db}` | **Tháng:** `{selected_month}`")

# Tính toán các dòng tài chính của tháng hiện tại
member_finance_rows, expenses, incomes, finance_summary = db.calculate_finance_rows(selected_month)

# --- THIẾT LẬP CÁC TAB CHÍNH ---
tab_overview, tab_matches, tab_finance, tab_settlement, tab_config = st.tabs([
    "🏠 TỔNG QUAN", 
    "🏆 TRẬN ĐẤU & XẾP HẠNG", 
    "💰 THU & CHI", 
    "📊 TÍNH TOÁN THÁNG", 
    "👥 HỘI VIÊN & CẤU HÌNH"
])

# =========================================================
# TAB 1: TỔNG QUAN (OVERVIEW)
# =========================================================
with tab_overview:
    # 4 thẻ metrics chính
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-title">Số dư quỹ (tích lũy)</div>
                <div class="metric-value" style="color:#10b981;">{finance_summary['fund_balance']:,.0f} đ</div>
                <div style="font-size:12px; color:#64748b;">Số dư lũy kế cuối tháng {selected_month}</div>
            </div>
            """,
            unsafe_allow_html=True
        )
    with col2:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-title">Tổng thu quỹ trong tháng</div>
                <div class="metric-value" style="color:#0ea5e9;">{finance_summary['total_income_fund']:,.0f} đ</div>
                <div style="font-size:12px; color:#64748b;">Thu hội viên + các khoản thu khác</div>
            </div>
            """,
            unsafe_allow_html=True
        )
    with col3:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-title">Tổng chi quỹ trong tháng</div>
                <div class="metric-value" style="color:#f43f5e;">{finance_summary['total_expense']:,.0f} đ</div>
                <div style="font-size:12px; color:#64748b;">Sân, bóng, lượm banh, ăn uống...</div>
            </div>
            """,
            unsafe_allow_html=True
        )
    with col4:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-title">Thặng dư tháng</div>
                <div class="metric-value" style="color:#eab308;">{finance_summary['fund_this_month']:,.0f} đ</div>
                <div style="font-size:12px; color:#64748b;">Thu - Chi của riêng tháng {selected_month}</div>
            </div>
            """,
            unsafe_allow_html=True
        )

    st.write("---")
    
    # Biểu đồ phân phối chi phí
    col_chart1, col_chart2 = st.columns(2)
    with col_chart1:
        st.subheader("📊 Chi tiết Cơ cấu Chi phí")
        expense_data = {
            "Khoản chi": ["Thuê sân", "Mua bóng", "Lượm banh", "Ăn uống", "Tiệc cuối tháng", "Khác"],
            "Số tiền (đ)": [
                finance_summary["court_total"],
                finance_summary["ball_total"],
                finance_summary["picker_total"],
                finance_summary["meal_total"],
                finance_summary["party_total"],
                finance_summary["other_total"]
            ]
        }
        df_exp = pd.DataFrame(expense_data)
        df_exp = df_exp[df_exp["Số tiền (đ)"] > 0]
        if not df_exp.empty:
            st.bar_chart(df_exp.set_index("Khoản chi"), color="#f43f5e")
        else:
            st.info("Chưa có dữ liệu chi phí nào cho tháng này.")

    with col_chart2:
        st.subheader("📈 Chi tiết nguồn Thu Quỹ")
        income_data = {
            "Khoản thu": ["Thu hội viên làm tròn", "Thu người ngoài", "Thu khác/Thu quỹ thủ công"],
            "Số tiền (đ)": [
                finance_summary["total_rounded_fee"],
                finance_summary["external_income_total"],
                finance_summary["other_income_total"]
            ]
        }
        df_inc = pd.DataFrame(income_data)
        df_inc = df_inc[df_inc["Số tiền (đ)"] > 0]
        if not df_inc.empty:
            st.bar_chart(df_inc.set_index("Khoản thu"), color="#0ea5e9")
        else:
            st.info("Chưa có dữ liệu thu quỹ nào cho tháng này.")


# =========================================================
# TAB 2: TRẬN ĐẤU & XẾP HẠNG (MATCHES & RANKINGS)
# =========================================================
with tab_matches:
    subtab_rank, subtab_history = st.tabs(["🏆 BẢNG XẾP HẠNG", "📝 LỊCH SỬ TRẬN ĐẤU"])
    
    with subtab_rank:
        st.subheader("Bảng xếp hạng thành tích & Tiền phạt")
        stats_list = db.calculate_stats(selected_month)
        if stats_list:
            df_stats = pd.DataFrame(stats_list)
            # Sắp xếp theo thứ tự: Điểm phạt giảm dần, số trận giảm dần
            df_stats["total_points"] = df_stats["wins"] * db.get_rules()["point_win"] + df_stats["draws"] * db.get_rules()["point_draw"]
            df_stats = df_stats.sort_values(by=["total_points", "wins", "gf"], ascending=[False, False, False])
            df_stats.insert(0, "Hạng", range(1, len(df_stats) + 1))
            
            # Đổi tên các cột hiển thị cho thân thiện
            df_stats_display = df_stats.rename(columns={
                "name": "Tên hội viên",
                "matches": "Trận",
                "wins": "Thắng",
                "draws": "Hòa",
                "losses": "Thua",
                "gf": "Bàn thắng",
                "ga": "Bàn thua",
                "match_money": "Tiền phạt trận",
                "bet_money": "Tiền độ",
                "money": "Tổng tiền",
                "total_points": "Điểm thành tích"
            })
            st.dataframe(df_stats_display.set_index("Hạng"), use_container_width=True)
        else:
            st.info("Không có dữ liệu trận đấu cho tháng này để tính xếp hạng.")

    with subtab_history:
        # Form thêm trận đấu mới
        with st.expander("➕ THÊM TRẬN ĐẤU MỚI", expanded=False):
            players = db.get_player_names()
            if not players:
                st.warning("Vui lòng thêm hội viên trước trong Tab 'Hội viên & Cấu hình'.")
            else:
                col_a, col_b = st.columns(2)
                with col_a:
                    st.markdown("**Đội A**")
                    a1 = st.selectbox("Người chơi A1", options=players, key="a1")
                    a2 = st.selectbox("Người chơi A2 (Để trống nếu đánh đơn)", options=[""] + players, key="a2")
                    score_a = st.number_input("Tỷ số Đội A", min_value=0, max_value=20, value=0, key="score_a")
                
                with col_b:
                    st.markdown("**Đội B**")
                    b1 = st.selectbox("Người chơi B1", options=players, key="b1")
                    b2 = st.selectbox("Người chơi B2 (Để trống nếu đánh đơn)", options=[""] + players, key="b2")
                    score_b = st.number_input("Tỷ số Đội B", min_value=0, max_value=20, value=0, key="score_b")
                
                col_info1, col_info2, col_info3 = st.columns(3)
                with col_info1:
                    match_date = st.date_input("Ngày đấu", value=datetime.now())
                with col_info2:
                    match_bet = st.number_input("Tiền độ (nếu có)", min_value=0, step=10000, value=0)
                with col_info3:
                    custom_fine = st.checkbox("Sử dụng tiền phạt tự chọn")
                    match_money = None
                    if custom_fine:
                        match_money = st.number_input("Nhập tiền phạt tự chọn (đ)", min_value=0, step=5000, value=20000)
                
                note = st.text_input("Ghi chú trận đấu")
                
                if st.button("Lưu trận đấu"):
                    if a1 == b1 or (a2 and a2 == b2) or (a2 and a1 == a2) or (b2 and b1 == b2):
                        st.error("Trùng lặp người chơi giữa các đội!")
                    else:
                        # Tính số trận trong ngày để sinh match_no
                        cur = db.conn.cursor()
                        d_str = match_date.strftime("%Y-%m-%d")
                        max_no = cur.execute("SELECT MAX(match_no) FROM matches WHERE match_date=?", (d_str,)).fetchone()[0]
                        match_no = (max_no or 0) + 1
                        
                        cur.execute(
                            """
                            INSERT INTO matches (match_date, match_no, team_a1, team_a2, team_b1, team_b2, score_a, score_b, match_money, created_at, match_bet, note)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (d_str, match_no, a1, a2 or "", b1, b2 or "", score_a, score_b, match_money, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), match_bet, note)
                        )
                        db.conn.commit()
                        st.success("Đã thêm trận đấu thành công!")
                        st.rerun()

        # Danh sách các trận đấu
        st.subheader("Danh sách trận đấu trong tháng")
        matches_raw = db.fetch_matches(selected_month)
        if matches_raw:
            df_matches = pd.DataFrame(matches_raw, columns=[
                "ID", "Ngày", "Số trận", "Đội A (1)", "Đội A (2)", "Đội B (1)", "Đội B (2)", "Điểm A", "Điểm B", "Phạt tự chọn", "Tiền độ", "Ghi chú"
            ])
            # Tạo hiển thị cột người chơi ghép
            df_matches["Đội A"] = df_matches.apply(lambda r: f"{r['Đội A (1)']} + {r['Đội A (2)']}" if r['Đội A (2)'] else r['Đội A (1)'], axis=1)
            df_matches["Đội B"] = df_matches.apply(lambda r: f"{r['Đội B (1)']} + {r['Đội B (2)']}" if r['Đội B (2)'] else r['Đội B (1)'], axis=1)
            df_matches["Tỷ số (A-B)"] = df_matches.apply(lambda r: f"{r['Điểm A']} - {r['Điểm B']}", axis=1)
            
            df_display = df_matches[["ID", "Ngày", "Số trận", "Đội A", "Tỷ số (A-B)", "Đội B", "Phạt tự chọn", "Tiền độ", "Ghi chú"]]
            st.dataframe(df_display.set_index("ID"), use_container_width=True)
            
            # Xoá trận đấu
            del_id = st.number_input("Nhập ID trận cần xoá:", min_value=1, step=1)
            if st.button("Xoá trận đấu chọn", type="primary"):
                db.conn.execute("DELETE FROM matches WHERE id=?", (del_id,))
                db.conn.commit()
                st.success(f"Đã xoá trận đấu ID {del_id}")
                st.rerun()
        else:
            st.info("Chưa có trận đấu nào được lưu trong tháng này.")


# =========================================================
# TAB 3: QUẢN LÝ THU & CHI (FINANCE INCOMES & EXPENSES)
# =========================================================
with tab_finance:
    subtab_expense, subtab_income = st.tabs(["💸 PHẦN CHI (EXPENSES)", "📥 PHẦN THU (INCOMES)"])
    
    with subtab_expense:
        # Form thêm chi phí mới
        with st.expander("➕ THÊM PHẦN CHI MỚI", expanded=False):
            players = db.get_player_names()
            exp_date = st.date_input("Ngày chi", value=datetime.now(), key="exp_date")
            exp_type = st.selectbox("Loại chi phí", options=EXPENSE_TYPES)
            exp_amount = st.number_input("Số tiền chi (đ)", min_value=0, step=10000, value=0)
            
            # Người chi
            payers_options = ["Quỹ"] + players
            exp_payer = st.selectbox("Người trả tiền (Payer)", options=payers_options)
            
            # Đối tượng chia tiền
            exp_sharing_type = st.radio("Đối tượng cùng chia sẻ chi phí này:", ["Tất cả hội viên (All)", "Chọn hội viên cụ thể"])
            exp_participants = ["All"]
            if exp_sharing_type == "Chọn hội viên cụ thể":
                exp_participants = st.multiselect("Chọn những ai tham gia:", options=players)
                
            exp_note = st.text_input("Ghi chú chi", key="exp_note")
            
            if st.button("Lưu khoản chi"):
                if exp_amount <= 0:
                    st.error("Vui lòng nhập số tiền chi lớn hơn 0.")
                elif exp_sharing_type == "Chọn hội viên cụ thể" and not exp_participants:
                    st.error("Vui lòng chọn ít nhất 1 người tham gia chia sẻ tiền.")
                else:
                    parts_json = json.dumps(exp_participants)
                    db.conn.execute(
                        """
                        INSERT INTO finance_expenses (month, expense_date, expense_type, amount, participants, payer, note)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (selected_month, exp_date.strftime("%Y-%m-%d"), exp_type, exp_amount, parts_json, exp_payer, exp_note)
                    )
                    db.conn.commit()
                    st.success("Đã thêm chi phí thành công!")
                    st.rerun()

        # Hiển thị danh sách chi
        st.subheader(f"Danh sách khoản chi quỹ trong tháng {selected_month}")
        if expenses:
            df_expenses = pd.DataFrame(expenses, columns=["ID", "Ngày chi", "Loại chi phí", "Số tiền (đ)", "Người tham gia", "Người trả", "Ghi chú"])
            # Format cột danh sách người tham gia
            df_expenses["Người tham gia"] = df_expenses["Người tham gia"].apply(lambda x: ", ".join(load_json_list(x)) if "All" not in load_json_list(x) else "Tất cả (All)")
            st.dataframe(df_expenses.set_index("ID"), use_container_width=True)
            
            # Xoá khoản chi
            del_exp_id = st.number_input("Nhập ID khoản chi cần xoá:", min_value=1, step=1, key="del_exp")
            if st.button("Xoá khoản chi chọn", type="primary"):
                db.conn.execute("DELETE FROM finance_expenses WHERE id=?", (del_exp_id,))
                db.conn.commit()
                st.success(f"Đã xoá khoản chi ID {del_exp_id}")
                st.rerun()
        else:
            st.info("Chưa có khoản chi nào trong tháng này.")

    with subtab_income:
        # Form thêm khoản thu mới
        with st.expander("➕ THÊM KHOẢN THU MỚI", expanded=False):
            players = db.get_player_names()
            inc_date = st.date_input("Ngày thu", value=datetime.now(), key="inc_date")
            inc_type = st.selectbox("Loại khoản thu", options=DEFAULT_INCOME_TYPES)
            inc_amount = st.number_input("Số tiền thu (đ)", min_value=0, step=10000, value=0, key="inc_amount")
            
            # Người thu
            inc_collector = st.selectbox("Người thu hộ tiền (Collector)", options=["Quỹ"] + players, key="inc_collector")
            inc_note = st.text_input("Ghi chú thu / Người đóng tiền", key="inc_note")
            
            if st.button("Lưu khoản thu"):
                if inc_amount <= 0:
                    st.error("Vui lòng nhập số tiền thu lớn hơn 0.")
                else:
                    db.conn.execute(
                        """
                        INSERT INTO finance_incomes (month, income_date, income_type, amount, collector, note)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (selected_month, inc_date.strftime("%Y-%m-%d"), inc_type, inc_amount, inc_collector, inc_note)
                    )
                    db.conn.commit()
                    st.success("Đã thêm khoản thu thành công!")
                    st.rerun()

        # Hiển thị danh sách thu
        st.subheader(f"Danh sách các khoản thu quỹ trong tháng {selected_month}")
        if incomes:
            df_incomes = pd.DataFrame(incomes, columns=["ID", "Ngày thu", "Loại khoản thu", "Số tiền (đ)", "Người thu", "Ghi chú"])
            st.dataframe(df_incomes.set_index("ID"), use_container_width=True)
            
            # Xoá khoản thu
            del_inc_id = st.number_input("Nhập ID khoản thu cần xoá:", min_value=1, step=1, key="del_inc")
            if st.button("Xoá khoản thu chọn", type="primary"):
                db.conn.execute("DELETE FROM finance_incomes WHERE id=?", (del_inc_id,))
                db.conn.commit()
                st.success(f"Đã xoá khoản thu ID {del_inc_id}")
                st.rerun()
        else:
            st.info("Chưa có khoản thu thủ công nào trong tháng này.")


# =========================================================
# TAB 4: BẢNG TÍNH TIỀN THÁNG (MONTHLY SETTLEMENT)
# =========================================================
with tab_settlement:
    st.subheader(f"Báo cáo Quyết toán chi tiết - Tháng {selected_month}")
    
    # Bảng hội viên chính thức
    st.markdown("#### 1. Phần Hội viên chính thức")
    if member_finance_rows:
        df_mem_fin = pd.DataFrame(member_finance_rows)
        # Đổi tên cột cho thân thiện
        df_mem_fin_display = df_mem_fin.rename(columns={
            "name": "Hội viên",
            "weekly": "Buổi/Tuần",
            "court": "Tiền sân",
            "ball_picker": "Bóng + Nhặt",
            "meal": "Ăn uống",
            "other_charge": "Chi khác",
            "match_money": "Tiền phạt",
            "bet_money": "Tiền độ",
            "calc_fee": "Phí thực tính",
            "rounded_fee": "Phí làm tròn",
            "paid_by_member": "Đã chi hộ",
            "collected_by_member": "Đã thu hộ",
            "net_payable": "Thu tháng (Cần đóng)"
        })
        # Format hiển thị
        cols_to_show = ["Hội viên", "Buổi/Tuần", "Tiền sân", "Bóng + Nhặt", "Ăn uống", "Chi khác", "Tiền phạt", "Tiền độ", "Phí thực tính", "Phí làm tròn", "Đã chi hộ", "Đã thu hộ", "Thu tháng (Cần đóng)"]
        st.dataframe(df_mem_fin_display[cols_to_show].set_index("Hội viên"), use_container_width=True)
        
        # Download Excel
        excel_buffer = BytesIO()
        df_mem_fin_display[cols_to_show].to_excel(excel_buffer, index=False)
        st.download_button(
            label="📥 Tải Bảng Hội Viên (Excel)",
            data=excel_buffer.getvalue(),
            file_name=f"Bao_cao_hoi_vien_{selected_month}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        st.info("Chưa có danh sách hội viên trong tháng.")

    # Bảng người ngoài vãng lai
    st.write("---")
    st.markdown("#### 2. Phần Người ngoài / Vãng lai")
    ext_rows = finance_summary["external_rows"]
    if ext_rows:
        df_ext = pd.DataFrame(ext_rows)
        df_ext_display = df_ext.rename(columns={
            "name": "Họ và tên",
            "meal": "Tiền ăn uống",
            "other": "Khoản chi khác",
            "paid_by_external": "Đã chi hộ",
            "collected_by_external": "Đã thu hộ",
            "net_payable": "Cần thu/trả lại"
        })
        st.dataframe(df_ext_display.set_index("Họ và tên"), use_container_width=True)
    else:
        st.info("Không phát sinh khoản thu chi nào của người ngoài trong tháng này.")

    # Phần tổng hợp dòng tiền cuối tháng
    st.write("---")
    st.markdown("#### 3. Bảng tổng hợp quỹ cuối kỳ")
    col_sum1, col_sum2 = st.columns(2)
    with col_sum1:
        st.markdown(
            f"""
            - **Số dư quỹ tháng trước chuyển sang:** `{finance_summary['prev_fund']:,.0f} đ`
            - **Tổng thu quỹ trong tháng này:** `{finance_summary['total_income_fund']:,.0f} đ`
                - Trong đó thu hội viên làm tròn: `{finance_summary['total_rounded_fee']:,.0f} đ`
                - Thu người ngoài vãng lai: `{finance_summary['external_income_total']:,.0f} đ`
                - Thu ngoài khác: `{finance_summary['other_income_total']:,.0f} đ`
            """
        )
    with col_sum2:
        st.markdown(
            f"""
            - **Tổng chi tiêu của quỹ trong tháng:** `{finance_summary['total_expense']:,.0f} đ`
            - **Thặng dư tích lũy cuối tháng này:** `{finance_summary['fund_balance']:,.0f} đ`
            - **Tiền dư ra do làm tròn 10k:** `{finance_summary['round_extra']:,.0f} đ`
            """
        )


# =========================================================
# TAB 5: HỘI VIÊN & CẤU HÌNH (MEMBERS & CONFIGURATION)
# =========================================================
with tab_config:
    subtab_members, subtab_rules = st.tabs(["👥 DANH SÁCH HỘI VIÊN", "⚙️ LUẬT & CẤU HÌNH"])
    
    with subtab_members:
        col_m1, col_m2 = st.columns([2, 1])
        with col_m1:
            st.subheader("Quản lý danh sách hội viên")
            cur = db.conn.cursor()
            raw_players = cur.execute("SELECT id, name, active, weekly_sessions FROM players ORDER BY name").fetchall()
            
            if raw_players:
                df_players = pd.DataFrame(raw_players, columns=["ID", "Họ và tên", "Đang hoạt động (1=Có, 0=Không)", "Số buổi/Tuần"])
                st.dataframe(df_players.set_index("ID"), use_container_width=True)
                
                # Biểu mẫu xoá hội viên
                del_p_id = st.number_input("Nhập ID hội viên cần xoá:", min_value=1, step=1, key="del_p")
                if st.button("Xoá hội viên", type="primary"):
                    db.conn.execute("DELETE FROM players WHERE id=?", (del_p_id,))
                    db.conn.commit()
                    st.success(f"Đã xoá hội viên ID {del_p_id}")
                    st.rerun()
            else:
                st.info("Chưa có hội viên nào trong danh sách.")

        with col_m2:
            st.subheader("Thêm hội viên mới")
            new_p_name = st.text_input("Họ và tên hội viên:")
            new_p_weekly = st.selectbox("Số buổi/Tuần:", options=[3, 2, 1], index=0)
            
            if st.button("Thêm hội viên"):
                if not new_p_name.strip():
                    st.error("Vui lòng nhập tên hội viên.")
                else:
                    try:
                        db.conn.execute(
                            "INSERT INTO players(name, active, weekly_sessions) VALUES (?, 1, ?)",
                            (new_p_name.strip(), new_p_weekly)
                        )
                        db.conn.commit()
                        st.success(f"Đã thêm: {new_p_name}")
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("Tên hội viên đã tồn tại!")

    with subtab_rules:
        st.subheader("Cấu hình mức phạt & Tính điểm")
        current_rules = db.get_rules()
        
        rule_lose = st.number_input("Tiền phạt khi thua trận (đ):", value=current_rules["fine_lose"], step=5000)
        rule_draw = st.number_input("Tiền phạt khi hòa trận (đ):", value=current_rules["fine_draw"], step=5000)
        rule_zero = st.number_input("Tiền phạt khi thua trắng 0 bàn (đ):", value=current_rules["fine_lose_zero"], step=5000)
        
        rule_pt_win = st.number_input("Điểm cộng khi Thắng:", value=current_rules["point_win"], step=1)
        rule_pt_draw = st.number_input("Điểm cộng khi Hòa:", value=current_rules["point_draw"], step=1)
        rule_pt_loss = st.number_input("Điểm cộng khi Thua:", value=current_rules["point_loss"], step=1)
        
        # Cấu hình người gom quỹ tự động
        st.write("---")
        st.subheader("Thiết lập thủ quỹ tự động thu")
        players_names = db.get_player_names()
        
        auto_collector_rounded = st.selectbox(
            "Người thu hộ tiền hội viên làm tròn:",
            options=["Quỹ"] + players_names,
            index=(["Quỹ"] + players_names).index(db.get_auto_income_collector(AUTO_ROUNDED_MEMBER_INCOME, selected_month))
        )
        
        auto_collector_external = st.selectbox(
            "Người thu hộ tiền người ngoài vãng lai:",
            options=["Quỹ"] + players_names,
            index=(["Quỹ"] + players_names).index(db.get_auto_income_collector(AUTO_EXTERNAL_INCOME, selected_month))
        )

        if st.button("Lưu cấu hình"):
            new_rules = {
                "fine_lose": rule_lose,
                "fine_draw": rule_draw,
                "fine_lose_zero": rule_zero,
                "point_win": rule_pt_win,
                "point_draw": rule_pt_draw,
                "point_loss": rule_pt_loss,
            }
            db.save_rules(new_rules)
            
            # Lưu thủ quỹ tự động
            db.conn.execute(
                "INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
                (f"auto_income_collector::{selected_month}::{AUTO_ROUNDED_MEMBER_INCOME}", auto_collector_rounded)
            )
            db.conn.execute(
                "INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
                (f"auto_income_collector::{selected_month}::{AUTO_EXTERNAL_INCOME}", auto_collector_external)
            )
            db.conn.commit()
            
            st.success("Đã lưu các thiết lập cấu hình mới!")
            st.rerun()

# Đóng kết nối khi ứng dụng chạy xong
db.conn.close()
