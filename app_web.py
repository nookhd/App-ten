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
    page_title="Tennis Vui - Quản lý Tài chính & Giải đấu",
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

    def validate_match_data(self, data):
        try:
            datetime.strptime(data["match_date"], "%Y-%m-%d")
        except ValueError:
            raise ValueError("Ngày phải có dạng YYYY-MM-DD")
        team_a_names = [data["team_a1"].strip(), data["team_a2"].strip()]
        team_b_names = [data["team_b1"].strip(), data["team_b2"].strip()]
        team_a_used = [n for n in team_a_names if n]
        team_b_used = [n for n in team_b_names if n]

        if not team_a_used or not team_b_used:
            raise ValueError("Vui lòng nhập ít nhất 1 người cho mỗi đội")
        if len(team_a_used) != len(team_b_used):
            raise ValueError("Trận đánh đơn nhập 1 người mỗi đội; trận đánh đôi nhập 2 người mỗi đội")

        all_names = team_a_used + team_b_used
        if len(set(n.lower() for n in all_names)) < len(all_names):
            raise ValueError("Một người không được xuất hiện 2 lần trong cùng một trận")
        if data["score_a"] < 0 or data["score_b"] < 0 or data["score_a"] > 7 or data["score_b"] > 7:
            raise ValueError("Số game nên nằm trong khoảng 0 đến 7")
        if data.get("match_money") is not None and data["match_money"] < 0:
            raise ValueError("Tiền trận không được nhỏ hơn 0")
        if data.get("match_bet", 0) < 0:
            raise ValueError("Tiền độ không được nhỏ hơn 0")
        if data["score_a"] == 0 and data["score_b"] == 0:
            raise ValueError("Tỷ số 0-0 không hợp lệ")

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


# --- EXCEL & PDF EXPORTERS ---
def export_excel_bytes(db, month):
    matches = db.fetch_matches(month)
    df_matches = pd.DataFrame(matches, columns=[
        "ID", "Ngày", "Số trận", "A1", "A2", "B1", "B2", "Điểm A", "Điểm B", "Phạt tự chọn", "Tiền độ", "Ghi chú"
    ])
    
    stats = db.calculate_stats(month)
    df_stats = pd.DataFrame(stats)
    if not df_stats.empty:
        df_stats["total_points"] = df_stats["wins"] * db.get_rules()["point_win"] + df_stats["draws"] * db.get_rules()["point_draw"]
        df_stats = df_stats.sort_values(by=["total_points", "wins", "gf"], ascending=[False, False, False])
        df_stats.insert(0, "Hạng", range(1, len(df_stats) + 1))
        df_stats = df_stats.rename(columns={
            "name": "Tên hội viên", "matches": "Trận", "wins": "Thắng", "draws": "Hòa", "losses": "Thua",
            "gf": "Bàn thắng", "ga": "Bàn thua", "match_money": "Tiền phạt trận", "bet_money": "Tiền độ",
            "money": "Tổng tiền", "total_points": "Điểm thành tích"
        })
    
    rows, expenses, incomes, summary = db.calculate_finance_rows(month)
    df_members = pd.DataFrame(rows)
    if not df_members.empty:
        df_members = df_members.rename(columns={
            "name": "Hội viên", "weekly": "Buổi/Tuần", "court": "Tiền sân", "ball_picker": "Bóng + Nhặt",
            "meal": "Ăn uống", "other_charge": "Chi khác", "match_money": "Tiền phạt", "bet_money": "Tiền độ",
            "calc_fee": "Phí thực tính", "rounded_fee": "Phí làm tròn", "paid_by_member": "Đã chi hộ",
            "collected_by_member": "Đã thu hộ", "net_payable": "Thu tháng (Cần đóng)"
        })
        
    df_ext = pd.DataFrame(summary["external_rows"])
    if not df_ext.empty:
        df_ext = df_ext.rename(columns={
            "name": "Họ và tên", "meal": "Tiền ăn uống", "other": "Khoản chi khác",
            "paid_by_external": "Đã chi hộ", "collected_by_external": "Đã thu hộ", "net_payable": "Cần thu/trả lại"
        })
        
    df_expenses = pd.DataFrame(expenses, columns=["ID", "Ngày chi", "Loại chi phí", "Số tiền (đ)", "Người tham gia", "Người trả", "Ghi chú"])
    if not df_expenses.empty:
        df_expenses["Người tham gia"] = df_expenses["Người tham gia"].apply(lambda x: ", ".join(load_json_list(x)) if "All" not in load_json_list(x) else "Tất cả (All)")
        
    df_incomes = pd.DataFrame(incomes, columns=["ID", "Ngày thu", "Loại khoản thu", "Số tiền (đ)", "Người thu", "Ghi chú"])
    
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_matches.to_excel(writer, sheet_name="Trận đấu", index=False)
        if not df_stats.empty:
            df_stats.to_excel(writer, sheet_name="Xếp hạng", index=False)
        if not df_members.empty:
            df_members.to_excel(writer, sheet_name="Quyết toán Hội viên", index=False)
        if not df_ext.empty:
            df_ext.to_excel(writer, sheet_name="Quyết toán Vãng lai", index=False)
        if not df_expenses.empty:
            df_expenses.to_excel(writer, sheet_name="Khoản Chi", index=False)
        if not df_incomes.empty:
            df_incomes.to_excel(writer, sheet_name="Khoản Thu", index=False)
        
    return output.getvalue()


def export_pdf_bytes(db, month):
    output = BytesIO()
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
        font_name = "HYSMyeongJo-Medium"
    except Exception:
        font_name = "Helvetica"
        
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors
    
    doc = SimpleDocTemplate(output)
    styles = getSampleStyleSheet()
    elements = []
    
    title_text = f"Báo cáo Tennis Vui - Tháng {month}"
    title = Paragraph(f"<font name='{font_name}'><b>{title_text}</b></font>", styles["Title"])
    elements.append(title)
    elements.append(Spacer(1, 12))
    
    def add_pdf_table(title_text, df):
        elements.append(Paragraph(f"<font name='{font_name}'><b>{title_text}</b></font>", styles["Heading2"]))
        elements.append(Spacer(1, 6))
        if df.empty:
            elements.append(Paragraph(f"<font name='{font_name}'>Không có dữ liệu</font>", styles["Normal"]))
            elements.append(Spacer(1, 12))
            return
            
        headers = list(df.columns)
        data = [headers]
        for _, row in df.iterrows():
            data.append([str(v) for v in row.values])
            
        tbl = Table(data)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("GRID", (0, 0), (-1, -1), 1, colors.black),
            ("FONTNAME", (0, 0), (-1, -1), font_name),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE")
        ]))
        elements.append(tbl)
        elements.append(Spacer(1, 16))
        
    matches = db.fetch_matches(month)
    df_matches = pd.DataFrame(matches, columns=[
        "ID", "Ngày", "Trận số", "A1", "A2", "B1", "B2", "Điểm A", "Điểm B", "Phạt", "Độ", "Ghi chú"
    ])
    
    stats = db.calculate_stats(month)
    df_stats = pd.DataFrame(stats)
    if not df_stats.empty:
        df_stats["total_points"] = df_stats["wins"] * db.get_rules()["point_win"] + df_stats["draws"] * db.get_rules()["point_draw"]
        df_stats = df_stats.sort_values(by=["total_points", "wins", "gf"], ascending=[False, False, False])
        df_stats.insert(0, "Hạng", range(1, len(df_stats) + 1))
        df_stats = df_stats.rename(columns={
            "name": "Hội viên", "matches": "Trận", "wins": "T", "draws": "H", "losses": "B",
            "gf": "BT", "ga": "BT", "match_money": "Phạt", "bet_money": "Độ", "money": "Tổng"
        })
        
    rows, expenses, incomes, summary = db.calculate_finance_rows(month)
    df_members = pd.DataFrame(rows)
    if not df_members.empty:
        df_members = df_members.rename(columns={
            "name": "Hội viên", "weekly": "Buổi", "court": "Sân", "ball_picker": "Bóng",
            "meal": "Ăn", "other_charge": "Khác", "match_money": "Phạt", "bet_money": "Độ",
            "calc_fee": "Thực", "rounded_fee": "Tròn", "paid_by_member": "Chi hộ",
            "collected_by_member": "Thu hộ", "net_payable": "Thu tháng"
        })
        df_members = df_members[["Hội viên", "Buổi", "Sân", "Bóng", "Ăn", "Khác", "Phạt", "Độ", "Chi hộ", "Thu hộ", "Thu tháng"]]
        
    df_expenses = pd.DataFrame(expenses, columns=["ID", "Ngày", "Loại", "Số tiền", "Tham gia", "Người chi", "Ghi chú"])
    if not df_expenses.empty:
        df_expenses["Tham gia"] = df_expenses["Tham gia"].apply(lambda x: ", ".join(load_json_list(x)) if "All" not in load_json_list(x) else "All")
        df_expenses = df_expenses[["ID", "Ngày", "Loại", "Số tiền", "Tham gia", "Người chi"]]

    add_pdf_table("Thống kê trận đấu", df_matches)
    if not df_stats.empty:
        add_pdf_table("Bảng xếp hạng", df_stats)
    if not df_members.empty:
        add_pdf_table("Quyết toán hội viên", df_members)
    if not df_expenses.empty:
        add_pdf_table("Danh sách khoản chi", df_expenses)
    
    doc.build(elements)
    return output.getvalue()


# --- EXCEL IMPORTER ---
def import_excel_data(uploaded_file, db):
    try:
        from openpyxl import load_workbook
        wb = load_workbook(uploaded_file, data_only=True)
        ws = wb.active
        
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return False, "File Excel không có dữ liệu."
            
        header_aliases = {
            "match_date": {"ngay", "ngay_danh", "ngay_thi_dau", "date", "match_date"},
            "match_no": {"tran", "tran_so", "so_tran", "match_no"},
            "team_a1": {"a1", "doi_a1", "team_a1"},
            "team_a2": {"a2", "doi_a2", "team_a2"},
            "score_a": {"a", "ty_so_a", "diem_a", "score_a"},
            "score_b": {"b", "ty_so_b", "diem_b", "score_b"},
            "team_b1": {"b1", "doi_b1", "team_b1"},
            "team_b2": {"b2", "doi_b2", "team_b2"},
            "match_money": {"tien_tran", "tien", "match_money", "money"},
            "match_bet": {"tien_do", "do", "match_bet", "bet"},
            "note": {"ghi_chu", "ghichu", "note", "remark"},
        }
        
        default_order = [
            "match_date", "match_no", "team_a1", "team_a2",
            "score_a", "score_b", "team_b1", "team_b2", "match_money"
        ]
        
        first_row = rows[0]
        normalized = [normalize_text_key(v) for v in first_row]
        col_map = {}
        
        for idx, h in enumerate(normalized):
            for field, aliases in header_aliases.items():
                if h in aliases:
                    col_map[field] = idx
                    
        has_header = all(k in col_map for k in ["match_date", "team_a1", "team_b1", "score_a", "score_b"])
        
        data_rows = rows[1:] if has_header else rows
        if not has_header:
            col_map = {field: idx for idx, field in enumerate(default_order)}
            
        def cell(row, field, default=""):
            idx = col_map.get(field)
            if idx is None or idx >= len(row):
                return default
            value = row[idx]
            return "" if value is None else value
            
        def parse_date_value(value):
            if isinstance(value, datetime):
                return value.strftime("%Y-%m-%d")
            text_value = str(value).strip()
            if not text_value:
                raise ValueError("Thiếu ngày")
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
                try:
                    return datetime.strptime(text_value, fmt).strftime("%Y-%m-%d")
                except ValueError:
                    pass
            raise ValueError(f"Ngày '{text_value}' phải có dạng YYYY-MM-DD hoặc DD/MM/YYYY")
            
        def parse_money(val):
            if val is None or str(val).strip() == "":
                return None
            try:
                txt = str(val).replace(",", "").replace(".", "").replace("đ", "").replace(" ", "")
                return int(txt)
            except Exception:
                return None

        count = 0
        errors = []
        cur = db.conn.cursor()
        
        for excel_row_no, row in enumerate(data_rows, start=2 if has_header else 1):
            if not row or all(v is None or str(v).strip() == "" for v in row):
                continue
                
            try:
                match_date = parse_date_value(cell(row, "match_date"))
                
                match_no_raw = cell(row, "match_no", "")
                if str(match_no_raw).strip() == "":
                    match_no = cur.execute(
                        "SELECT COALESCE(MAX(match_no), 0) + 1 FROM matches WHERE match_date = ?",
                        (match_date,)
                    ).fetchone()[0]
                else:
                    match_no = int(float(str(match_no_raw).strip()))
                    
                money_raw = cell(row, "match_money", "")
                match_money = parse_money(money_raw)
                
                bet_raw = cell(row, "match_bet", "")
                match_bet = parse_money(bet_raw) if bet_raw else 0
                
                data = {
                    "match_date": match_date,
                    "match_no": match_no,
                    "team_a1": str(cell(row, "team_a1")).strip(),
                    "team_a2": str(cell(row, "team_a2")).strip(),
                    "team_b1": str(cell(row, "team_b1")).strip(),
                    "team_b2": str(cell(row, "team_b2")).strip(),
                    "score_a": int(float(str(cell(row, "score_a")).strip())),
                    "score_b": int(float(str(cell(row, "score_b")).strip())),
                    "match_money": match_money,
                    "match_bet": match_bet,
                    "note": str(cell(row, "note", "")).strip(),
                }
                
                db.validate_match_data(data)
                cur.execute(
                    """
                    INSERT INTO matches(match_date, match_no, team_a1, team_a2, team_b1, team_b2, score_a, score_b, match_money, match_bet, note, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        data["match_date"], data["match_no"], data["team_a1"], data["team_a2"], data["team_b1"], data["team_b2"],
                        data["score_a"], data["score_b"], data["match_money"], data["match_bet"], data["note"], datetime.now().isoformat(timespec="seconds")
                    )
                )
                count += 1
                
            except Exception as e:
                errors.append(f"Dòng {excel_row_no}: {e}")
                
        db.conn.commit()
        if errors:
            return True, f"Đã nhập {count} trận. Có {len(errors)} dòng lỗi:\n" + "\n".join(errors[:5])
        return True, f"Đã nhập thành công {count} trận từ Excel."
    except Exception as e:
        return False, f"Lỗi đọc file: {e}"


# --- INLINE GRID EDITOR SAVE LOGIC ---
def save_editor_changes(db, table_name, editor_key, df_original):
    if editor_key not in st.session_state:
        return True, "Không có thay đổi nào."
    
    changes = st.session_state[editor_key]
    if not changes or (not changes.get("edited_rows") and not changes.get("added_rows") and not changes.get("deleted_rows")):
        return True, "Không có thay đổi nào."
    
    cur = db.conn.cursor()
    try:
        # 1. Deletions
        if "deleted_rows" in changes and changes["deleted_rows"]:
            for row_idx in changes["deleted_rows"]:
                row_id = int(df_original.iloc[row_idx]["ID"])
                cur.execute(f"DELETE FROM {table_name} WHERE id = ?", (row_id,))
        
        # 2. Edits
        if "edited_rows" in changes and changes["edited_rows"]:
            for row_idx_str, col_changes in changes["edited_rows"].items():
                row_idx = int(row_idx_str)
                row_id = int(df_original.iloc[row_idx]["ID"])
                row_data = df_original.iloc[row_idx].to_dict()
                row_data.update(col_changes)
                
                if table_name == "players":
                    name = str(row_data["Họ và tên"]).strip()
                    if not name:
                        raise ValueError("Tên hội viên không được để trống!")
                    active = 1 if row_data["Hoạt động"] else 0
                    weekly = int(row_data["Số buổi/Tuần"])
                    cur.execute("UPDATE players SET name = ?, active = ?, weekly_sessions = ? WHERE id = ?", (name, active, weekly, row_id))
                
                elif table_name == "matches":
                    money_val = row_data["Phạt tự chọn"]
                    match_money = int(money_val) if money_val is not None and not pd.isna(money_val) else None
                    bet_val = row_data["Tiền độ"]
                    match_bet = int(bet_val) if bet_val is not None and not pd.isna(bet_val) else 0
                    
                    data = {
                        "match_date": str(row_data["Ngày"]).strip(),
                        "match_no": int(row_data["Trận số"]),
                        "team_a1": str(row_data["A1"]).strip(),
                        "team_a2": str(row_data["A2"]).strip() if not pd.isna(row_data["A2"]) else "",
                        "team_b1": str(row_data["B1"]).strip(),
                        "team_b2": str(row_data["B2"]).strip() if not pd.isna(row_data["B2"]) else "",
                        "score_a": int(row_data["Điểm A"]),
                        "score_b": int(row_data["Điểm B"]),
                        "match_money": match_money,
                        "match_bet": match_bet,
                        "note": str(row_data["Ghi chú"]).strip() if not pd.isna(row_data["Ghi chú"]) else "",
                    }
                    db.validate_match_data(data)
                    cur.execute(
                        """
                        UPDATE matches SET match_date = ?, match_no = ?, team_a1 = ?, team_a2 = ?, team_b1 = ?, team_b2 = ?,
                                           score_a = ?, score_b = ?, match_money = ?, match_bet = ?, note = ?
                        WHERE id = ?
                        """,
                        (data["match_date"], data["match_no"], data["team_a1"], data["team_a2"], data["team_b1"], data["team_b2"],
                         data["score_a"], data["score_b"], data["match_money"], data["match_bet"], data["note"], row_id)
                    )
                
                elif table_name == "finance_expenses":
                    amount = int(row_data["Số tiền (đ)"])
                    payer = str(row_data["Người trả"]).strip()
                    note = str(row_data["Ghi chú"]).strip() if not pd.isna(row_data["Ghi chú"]) else ""
                    expense_type = str(row_data["Loại chi phí"]).strip()
                    expense_date = str(row_data["Ngày chi"]).strip()
                    
                    parts_text = str(row_data["Người tham gia"]).strip()
                    if parts_text.lower() == "tất cả (all)" or parts_text.lower() == "all":
                        parts = ["All"]
                    else:
                        parts = [p.strip() for p in parts_text.split(",") if p.strip()]
                    parts_json = json.dumps(parts)
                    
                    cur.execute(
                        """
                        UPDATE finance_expenses SET expense_date = ?, expense_type = ?, amount = ?, participants = ?, payer = ?, note = ?
                        WHERE id = ?
                        """,
                        (expense_date, expense_type, amount, parts_json, payer, note, row_id)
                    )
                
                elif table_name == "finance_incomes":
                    amount = int(row_data["Số tiền (đ)"])
                    collector = str(row_data["Người thu"]).strip()
                    note = str(row_data["Ghi chú"]).strip() if not pd.isna(row_data["Ghi chú"]) else ""
                    income_type = str(row_data["Loại khoản thu"]).strip()
                    income_date = str(row_data["Ngày thu"]).strip()
                    
                    cur.execute(
                        """
                        UPDATE finance_incomes SET income_date = ?, income_type = ?, amount = ?, collector = ?, note = ?
                        WHERE id = ?
                        """,
                        (income_date, income_type, amount, collector, note, row_id)
                    )
        
        # 3. Additions
        if "added_rows" in changes and changes["added_rows"]:
            for row_data in changes["added_rows"]:
                if table_name == "players":
                    name = str(row_data.get("Họ và tên", "")).strip()
                    if not name:
                        raise ValueError("Tên hội viên không được để trống!")
                    active = 1 if row_data.get("Hoạt động", True) else 0
                    weekly = int(row_data.get("Số buổi/Tuần", 3))
                    cur.execute("INSERT INTO players(name, active, weekly_sessions) VALUES (?, ?, ?)", (name, active, weekly))
                
                elif table_name == "matches":
                    money_val = row_data.get("Phạt tự chọn")
                    match_money = int(money_val) if money_val is not None and not pd.isna(money_val) else None
                    bet_val = row_data.get("Tiền độ")
                    match_bet = int(bet_val) if bet_val is not None and not pd.isna(bet_val) else 0
                    
                    match_date_val = str(row_data.get("Ngày", datetime.now().strftime("%Y-%m-%d"))).strip()
                    match_no_val = row_data.get("Trận số")
                    if match_no_val is None or pd.isna(match_no_val):
                        max_no = cur.execute("SELECT COALESCE(MAX(match_no), 0) FROM matches WHERE match_date = ?", (match_date_val,)).fetchone()[0]
                        match_no = max_no + 1
                    else:
                        match_no = int(match_no_val)
                    
                    data = {
                        "match_date": match_date_val,
                        "match_no": match_no,
                        "team_a1": str(row_data.get("A1", "")).strip(),
                        "team_a2": str(row_data.get("A2", "")).strip(),
                        "team_b1": str(row_data.get("B1", "")).strip(),
                        "team_b2": str(row_data.get("B2", "")).strip(),
                        "score_a": int(row_data.get("Điểm A", 0)),
                        "score_b": int(row_data.get("Điểm B", 0)),
                        "match_money": match_money,
                        "match_bet": match_bet,
                        "note": str(row_data.get("Ghi chú", "")).strip(),
                    }
                    db.validate_match_data(data)
                    cur.execute(
                        """
                        INSERT INTO matches (match_date, match_no, team_a1, team_a2, team_b1, team_b2, score_a, score_b, match_money, match_bet, note, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (data["match_date"], data["match_no"], data["team_a1"], data["team_a2"], data["team_b1"], data["team_b2"],
                         data["score_a"], data["score_b"], data["match_money"], data["match_bet"], data["note"], datetime.now().isoformat(timespec="seconds"))
                    )
                
                elif table_name == "finance_expenses":
                    amount = int(row_data.get("Số tiền (đ)", 0))
                    payer = str(row_data.get("Người trả", "Quỹ")).strip()
                    note = str(row_data.get("Ghi chú", "")).strip()
                    expense_type = str(row_data.get("Loại chi phí", EXPENSE_TYPES[0])).strip()
                    expense_date = str(row_data.get("Ngày chi", datetime.now().strftime("%Y-%m-%d"))).strip()
                    
                    parts_text = str(row_data.get("Người tham gia", "All")).strip()
                    if parts_text.lower() == "all" or not parts_text:
                        parts = ["All"]
                    else:
                        parts = [p.strip() for p in parts_text.split(",") if p.strip()]
                    parts_json = json.dumps(parts)
                    month = expense_date[:7]
                    
                    cur.execute(
                        """
                        INSERT INTO finance_expenses (month, expense_date, expense_type, amount, participants, payer, note)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (month, expense_date, expense_type, amount, parts_json, payer, note)
                    )
                
                elif table_name == "finance_incomes":
                    amount = int(row_data.get("Số tiền (đ)", 0))
                    collector = str(row_data.get("Người thu", "Quỹ")).strip()
                    note = str(row_data.get("Ghi chú", "")).strip()
                    income_type = str(row_data.get("Loại khoản thu", DEFAULT_INCOME_TYPES[0])).strip()
                    income_date = str(row_data.get("Ngày thu", datetime.now().strftime("%Y-%m-%d"))).strip()
                    month = income_date[:7]
                    
                    cur.execute(
                        """
                        INSERT INTO finance_incomes (month, income_date, income_type, amount, collector, note)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (month, income_date, income_type, amount, collector, note)
                    )
        
        db.conn.commit()
        return True, "Đã lưu tất cả thay đổi thành công!"
    except Exception as e:
        db.conn.rollback()
        return False, f"Lỗi: {e}"


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
    conn = sqlite3.connect(DEFAULT_DB_FILE)
    conn.close()
    local_files.append(DEFAULT_DB_FILE)

# Cho phép tải file lên
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

# Khởi tạo db kết nối (cached)
@st.cache_resource
def get_db(db_path):
    return TennisDB(db_path)

db = get_db(selected_db)

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

# Nút sao lưu nhanh database
if st.sidebar.button("💾 Sao lưu (Backup)"):
    backup_name = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{selected_db}"
    shutil.copy(selected_db, backup_name)
    st.sidebar.success(f"Đã sao lưu thành: {backup_name}")

# Tiêu đề chính
st.markdown(f'<div class="header-gradient">TENNIS VUI</div>', unsafe_allow_html=True)
st.markdown(f"**Dữ liệu đang xem:** `{selected_db}` | **Tháng:** `{selected_month}`")

# Tính toán các dòng tài chính của tháng hiện tại
member_finance_rows, expenses, incomes, finance_summary = db.calculate_finance_rows(selected_month)
players_list = db.get_player_names()

# --- THIẾT LẬP CÁC TAB CHÍNH ---
tab_overview, tab_matches, tab_finance, tab_settlement, tab_config = st.tabs([
    "🏠 TỔNG QUAN", 
    "🏆 TRẬN ĐẤU & XẾP HẠNG", 
    "💰 THU & CHI", 
    "📊 QUYẾT TOÁN THÁNG", 
    "👥 HỘI VIÊN & CẤU HÌNH"
])

# =========================================================
# TAB 1: TỔNG QUAN (OVERVIEW)
# =========================================================
with tab_overview:
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-title">Số dư quỹ lũy kế</div>
                <div class="metric-value" style="color:#10b981;">{finance_summary['fund_balance']:,.0f} đ</div>
                <div style="font-size:12px; color:#64748b;">Lũy kế cuối tháng {selected_month}</div>
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
                <div style="font-size:12px; color:#64748b;">Hội viên đóng + thu khác</div>
            </div>
            """,
            unsafe_allow_html=True
        )
    with col3:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-title">Tổng chi tiêu trong tháng</div>
                <div class="metric-value" style="color:#f43f5e;">{finance_summary['total_expense']:,.0f} đ</div>
                <div style="font-size:12px; color:#64748b;">Tiền sân, bóng, lượm banh...</div>
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
                <div style="font-size:12px; color:#64748b;">Chênh lệch Thu - Chi tháng này</div>
            </div>
            """,
            unsafe_allow_html=True
        )

    st.write("---")
    
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
            df_stats["total_points"] = df_stats["wins"] * db.get_rules()["point_win"] + df_stats["draws"] * db.get_rules()["point_draw"]
            df_stats = df_stats.sort_values(by=["total_points", "wins", "gf"], ascending=[False, False, False])
            df_stats.insert(0, "Hạng", range(1, len(df_stats) + 1))
            
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
        # File excel uploader to import matches
        with st.expander("📥 NHẬP TRẬN ĐẤU TỪ EXCEL", expanded=False):
            excel_file = st.file_uploader("Chọn tệp tin Excel (.xlsx)", type=["xlsx"])
            if excel_file is not None:
                if st.button("Bắt đầu Nhập"):
                    success, msg = import_excel_data(excel_file, db)
                    if success:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)
                        
        # Form thêm trận đấu mới nhanh
        with st.expander("➕ THÊM TRẬN ĐẤU NHANH", expanded=False):
            if not players_list:
                st.warning("Vui lòng thêm hội viên trước.")
            else:
                col_a, col_b = st.columns(2)
                with col_a:
                    st.markdown("**Đội A**")
                    a1_select = st.selectbox("Người chơi A1", options=players_list + ["-- Nhập người ngoài --"], key="fast_a1")
                    if a1_select == "-- Nhập người ngoài --":
                        a1 = st.text_input("Nhập tên người ngoài A1", key="fast_a1_custom").strip()
                    else:
                        a1 = a1_select
                        
                    a2_select = st.selectbox("Người chơi A2 (Đơn để trống)", options=["", "-- Nhập người ngoài --"] + players_list, key="fast_a2")
                    if a2_select == "-- Nhập người ngoài --":
                        a2 = st.text_input("Nhập tên người ngoài A2", key="fast_a2_custom").strip()
                    else:
                        a2 = a2_select
                        
                    score_a = st.number_input("Tỷ số Đội A", min_value=0, max_value=7, value=0, key="fast_score_a")
                
                with col_b:
                    st.markdown("**Đội B**")
                    b1_select = st.selectbox("Người chơi B1", options=players_list + ["-- Nhập người ngoài --"], key="fast_b1")
                    if b1_select == "-- Nhập người ngoài --":
                        b1 = st.text_input("Nhập tên người ngoài B1", key="fast_b1_custom").strip()
                    else:
                        b1 = b1_select
                        
                    b2_select = st.selectbox("Người chơi B2 (Đơn để trống)", options=["", "-- Nhập người ngoài --"] + players_list, key="fast_b2")
                    if b2_select == "-- Nhập người ngoài --":
                        b2 = st.text_input("Nhập tên người ngoài B2", key="fast_b2_custom").strip()
                    else:
                        b2 = b2_select
                        
                    score_b = st.number_input("Tỷ số Đội B", min_value=0, max_value=7, value=0, key="fast_score_b")
                
                col_info1, col_info2, col_info3 = st.columns(3)
                with col_info1:
                    match_date = st.date_input("Ngày đấu", value=datetime.now(), key="fast_date")
                with col_info2:
                    match_bet = st.number_input("Tiền độ (đ)", min_value=0, step=10000, value=0, key="fast_bet")
                with col_info3:
                    custom_fine = st.checkbox("Sử dụng tiền phạt tự chọn", key="fast_custom_fine")
                    match_money = None
                    if custom_fine:
                        match_money = st.number_input("Nhập tiền phạt tự chọn (đ)", min_value=0, step=5000, value=20000, key="fast_money")
                
                note = st.text_input("Ghi chú trận đấu", key="fast_note")
                
                if st.button("Lưu trận đấu", key="fast_save_btn"):
                    try:
                        d_str = match_date.strftime("%Y-%m-%d")
                        cur = db.conn.cursor()
                        max_no = cur.execute("SELECT COALESCE(MAX(match_no), 0) FROM matches WHERE match_date=?", (d_str,)).fetchone()[0]
                        
                        data = {
                            "match_date": d_str,
                            "match_no": max_no + 1,
                            "team_a1": a1,
                            "team_a2": a2 or "",
                            "team_b1": b1,
                            "team_b2": b2 or "",
                            "score_a": score_a,
                            "score_b": score_b,
                            "match_money": match_money,
                            "match_bet": match_bet,
                            "note": note,
                        }
                        db.validate_match_data(data)
                        db.conn.execute(
                            """
                            INSERT INTO matches (match_date, match_no, team_a1, team_a2, team_b1, team_b2, score_a, score_b, match_money, created_at, match_bet, note)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (data["match_date"], data["match_no"], data["team_a1"], data["team_a2"], data["team_b1"], data["team_b2"],
                             data["score_a"], data["score_b"], data["match_money"], datetime.now().isoformat(timespec="seconds"), data["match_bet"], data["note"])
                        )
                        db.conn.commit()
                        st.success("Đã thêm trận đấu thành công!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Lỗi: {e}")

        # Bảng chỉnh sửa inline lịch sử trận đấu
        st.subheader("Bảng chỉnh sửa Lịch sử trận đấu")
        matches_raw = db.fetch_matches(selected_month)
        if matches_raw:
            df_matches = pd.DataFrame(matches_raw, columns=[
                "ID", "Ngày", "Trận số", "A1", "A2", "B1", "B2", "Điểm A", "Điểm B", "Phạt tự chọn", "Tiền độ", "Ghi chú"
            ])
            
            # Cho phép chỉnh sửa bảng trực tiếp
            edited_matches_df = st.data_editor(
                df_matches,
                num_rows="dynamic",
                key="matches_editor",
                column_config={
                    "ID": st.column_config.NumberColumn("ID", disabled=True),
                    "Ngày": st.column_config.TextColumn("Ngày"),
                    "Trận số": st.column_config.NumberColumn("Trận số"),
                    "A1": st.column_config.TextColumn("A1"),
                    "A2": st.column_config.TextColumn("A2"),
                    "B1": st.column_config.TextColumn("B1"),
                    "B2": st.column_config.TextColumn("B2"),
                    "Điểm A": st.column_config.NumberColumn("Điểm A", min_value=0, max_value=7),
                    "Điểm B": st.column_config.NumberColumn("Điểm B", min_value=0, max_value=7),
                    "Phạt tự chọn": st.column_config.NumberColumn("Phạt tự chọn"),
                    "Tiền độ": st.column_config.NumberColumn("Tiền độ"),
                    "Ghi chú": st.column_config.TextColumn("Ghi chú")
                },
                use_container_width=True
            )
            
            col_save1, col_save2 = st.columns([1, 5])
            with col_save1:
                if st.button("Lưu thay đổi trận đấu", type="primary"):
                    success, msg = save_editor_changes(db, "matches", "matches_editor", df_matches)
                    if success:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)
            with col_save2:
                st.info("💡 Bạn có thể chỉnh sửa trực tiếp, thêm dòng cuối bảng hoặc xoá các dòng bằng cách tick chọn cột bên trái rồi nhấn phím Delete trên bàn phím. Sau đó nhấn 'Lưu thay đổi'.")
        else:
            st.info("Chưa có trận đấu nào được lưu trong tháng này.")


# =========================================================
# TAB 3: QUẢN LÝ THU & CHI (FINANCE INCOMES & EXPENSES)
# =========================================================
with tab_finance:
    subtab_expense, subtab_income = st.tabs(["💸 PHẦN CHI (EXPENSES)", "📥 PHẦN THU (INCOMES)"])
    
    with subtab_expense:
        # Bảng chỉnh sửa chi phí
        st.subheader("Bảng chỉnh sửa chi tiết các khoản chi quỹ")
        if expenses:
            df_expenses = pd.DataFrame(expenses, columns=["ID", "Ngày chi", "Loại chi phí", "Số tiền (đ)", "Người tham gia", "Người trả", "Ghi chú"])
            df_expenses["Người tham gia"] = df_expenses["Người tham gia"].apply(lambda x: ", ".join(load_json_list(x)) if "All" not in load_json_list(x) else "All")
            
            edited_exp_df = st.data_editor(
                df_expenses,
                num_rows="dynamic",
                key="expenses_editor",
                column_config={
                    "ID": st.column_config.NumberColumn("ID", disabled=True),
                    "Ngày chi": st.column_config.TextColumn("Ngày chi"),
                    "Loại chi phí": st.column_config.SelectboxColumn("Loại chi phí", options=EXPENSE_TYPES),
                    "Số tiền (đ)": st.column_config.NumberColumn("Số tiền (đ)"),
                    "Người tham gia": st.column_config.TextColumn("Người tham gia"),
                    "Người trả": st.column_config.TextColumn("Người trả"),
                    "Ghi chú": st.column_config.TextColumn("Ghi chú")
                },
                use_container_width=True
            )
            
            if st.button("Lưu thay đổi khoản chi", type="primary"):
                success, msg = save_editor_changes(db, "finance_expenses", "expenses_editor", df_expenses)
                if success:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)
        else:
            st.info("Chưa có khoản chi nào trong tháng này. Bạn có thể thêm dòng trực tiếp ở lưới trống dưới đây.")
            # Tạo DF trống để cho phép thêm dòng
            df_empty = pd.DataFrame(columns=["ID", "Ngày chi", "Loại chi phí", "Số tiền (đ)", "Người tham gia", "Người trả", "Ghi chú"])
            st.data_editor(df_empty, num_rows="dynamic", key="expenses_editor", use_container_width=True)
            if st.button("Lưu thay đổi khoản chi", type="primary"):
                success, msg = save_editor_changes(db, "finance_expenses", "expenses_editor", df_empty)
                if success:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

    with subtab_income:
        # Bảng chỉnh sửa khoản thu
        st.subheader("Bảng chỉnh sửa chi tiết các khoản thu quỹ")
        if incomes:
            df_incomes = pd.DataFrame(incomes, columns=["ID", "Ngày thu", "Loại khoản thu", "Số tiền (đ)", "Người thu", "Ghi chú"])
            
            edited_inc_df = st.data_editor(
                df_incomes,
                num_rows="dynamic",
                key="incomes_editor",
                column_config={
                    "ID": st.column_config.NumberColumn("ID", disabled=True),
                    "Ngày thu": st.column_config.TextColumn("Ngày thu"),
                    "Loại khoản thu": st.column_config.SelectboxColumn("Loại khoản thu", options=DEFAULT_INCOME_TYPES),
                    "Số tiền (đ)": st.column_config.NumberColumn("Số tiền (đ)"),
                    "Người thu": st.column_config.TextColumn("Người thu"),
                    "Ghi chú": st.column_config.TextColumn("Ghi chú")
                },
                use_container_width=True
            )
            
            if st.button("Lưu thay đổi khoản thu", type="primary"):
                success, msg = save_editor_changes(db, "finance_incomes", "incomes_editor", df_incomes)
                if success:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)
        else:
            st.info("Chưa có khoản thu nào trong tháng này. Bạn có thể thêm dòng trực tiếp ở lưới trống dưới đây.")
            df_empty = pd.DataFrame(columns=["ID", "Ngày thu", "Loại khoản thu", "Số tiền (đ)", "Người thu", "Ghi chú"])
            st.data_editor(df_empty, num_rows="dynamic", key="incomes_editor", use_container_width=True)
            if st.button("Lưu thay đổi khoản thu", type="primary"):
                success, msg = save_editor_changes(db, "finance_incomes", "incomes_editor", df_empty)
                if success:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)


# =========================================================
# TAB 4: BẢNG QUYẾT TOÁN THÁNG (SETTLEMENT & REPORTS)
# =========================================================
with tab_settlement:
    st.subheader(f"Báo cáo Quyết toán chi tiết - Tháng {selected_month}")
    
    # Nút tải báo cáo Excel và PDF
    col_dl1, col_dl2 = st.columns(2)
    with col_dl1:
        # Xuất file Excel đa dạng sheet
        excel_data = export_excel_bytes(db, selected_month)
        st.download_button(
            label="📥 Tải xuống Báo cáo Excel đầy đủ (6 sheets)",
            data=excel_data,
            file_name=f"Quyet_toan_Tennis_{selected_month}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )
    with col_dl2:
        # Xuất file PDF bằng reportlab
        pdf_data = export_pdf_bytes(db, selected_month)
        st.download_button(
            label="📥 Tải xuống Báo cáo PDF",
            data=pdf_data,
            file_name=f"Bao_cao_Tennis_{selected_month}.pdf",
            mime="application/pdf",
            use_container_width=True
        )
    
    st.write("---")

    # Bảng hội viên chính thức
    st.markdown("#### 1. Phần Hội viên chính thức")
    if member_finance_rows:
        df_mem_fin = pd.DataFrame(member_finance_rows)
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
        cols_to_show = ["Hội viên", "Buổi/Tuần", "Tiền sân", "Bóng + Nhặt", "Ăn uống", "Chi khác", "Tiền phạt", "Tiền độ", "Phí thực tính", "Phí làm tròn", "Đã chi hộ", "Đã thu hộ", "Thu tháng (Cần đóng)"]
        st.dataframe(df_mem_fin_display[cols_to_show].set_index("Hội viên"), use_container_width=True)
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
        st.subheader("Bảng quản lý hội viên chính thức")
        cur = db.conn.cursor()
        raw_players = cur.execute("SELECT id, name, active, weekly_sessions FROM players ORDER BY name").fetchall()
        
        df_players = pd.DataFrame(raw_players, columns=["ID", "Họ và tên", "Hoạt động", "Số buổi/Tuần"])
        df_players["Hoạt động"] = df_players["Hoạt động"].apply(lambda x: True if x == 1 else False)
        
        edited_players_df = st.data_editor(
            df_players,
            num_rows="dynamic",
            key="players_editor",
            column_config={
                "ID": st.column_config.NumberColumn("ID", disabled=True),
                "Họ và tên": st.column_config.TextColumn("Họ và tên"),
                "Hoạt động": st.column_config.CheckboxColumn("Hoạt động"),
                "Số buổi/Tuần": st.column_config.SelectboxColumn("Số buổi/Tuần", options=[1, 2, 3])
            },
            use_container_width=True
        )
        
        if st.button("Lưu thay đổi hội viên", type="primary"):
            success, msg = save_editor_changes(db, "players", "players_editor", df_players)
            if success:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)

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
