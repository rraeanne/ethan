import os
import sqlite3
from datetime import date, datetime, time, timedelta

DB_PATH = os.getenv('DATABASE_PATH', 'bot/expenses.db')

def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _ensure_column(cursor, table_name, column_name, definition):
    try:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
    except sqlite3.OperationalError:
        # Column already exists in existing deployments.
        pass

def _current_week_start():
    today = date.today()
    return today - timedelta(days=today.weekday())

def _normalize_week_start(week_start=None):
    if week_start is None:
        return _current_week_start()

    if isinstance(week_start, date):
        return week_start - timedelta(days=week_start.weekday())

    if isinstance(week_start, datetime):
        week_date = week_start.date()
        return week_date - timedelta(days=week_date.weekday())

    parsed = datetime.fromisoformat(str(week_start)).date()
    return parsed - timedelta(days=parsed.weekday())

def _week_bounds(week_start=None):
    week_date = _normalize_week_start(week_start)
    start_dt = datetime.combine(week_date, time.min)
    end_dt = start_dt + timedelta(days=7)
    return start_dt.strftime('%Y-%m-%d %H:%M:%S'), end_dt.strftime('%Y-%m-%d %H:%M:%S'), week_date

def _expense_week_start(created_at):
    parsed = datetime.fromisoformat(str(created_at))
    week_start = parsed.date() - timedelta(days=parsed.date().weekday())
    return week_start.isoformat()

def init_db():
    """Initialize the database with required tables and lightweight migrations."""
    conn = _get_conn()
    cursor = conn.cursor()

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        '''
    )

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS partners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_a INTEGER NOT NULL,
            user_b INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_a, user_b),
            FOREIGN KEY (user_a) REFERENCES users (user_id),
            FOREIGN KEY (user_b) REFERENCES users (user_id)
        )
        '''
    )

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            description TEXT,
            category TEXT,
            paid_by TEXT,
            is_shared INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
        '''
    )

    # Keep older databases compatible.
    _ensure_column(cursor, 'expenses', 'paid_by', 'TEXT')
    _ensure_column(cursor, 'expenses', 'is_shared', 'INTEGER DEFAULT 0')

    conn.commit()
    conn.close()

def add_or_get_user(user_id, username):
    """Add or update a user."""
    normalized_username = username.lower().lstrip('@') if username else None
    conn = _get_conn()
    cursor = conn.cursor()

    cursor.execute(
        '''
        INSERT INTO users (user_id, username)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET username = excluded.username
        ''',
        (user_id, normalized_username),
    )

    conn.commit()
    conn.close()

def set_partner_by_username(user_id, partner_username):
    """Link two users as partners. Partner must have started the bot already."""
    normalized = partner_username.lower().lstrip('@')

    conn = _get_conn()
    cursor = conn.cursor()

    cursor.execute('SELECT user_id FROM users WHERE username = ?', (normalized,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False, 'Partner not found. Ask them to /start the bot first.'

    partner_id = row['user_id']
    if partner_id == user_id:
        conn.close()
        return False, 'You cannot set yourself as partner.'

    user_a = min(user_id, partner_id)
    user_b = max(user_id, partner_id)

    cursor.execute('INSERT OR IGNORE INTO partners (user_a, user_b) VALUES (?, ?)', (user_a, user_b))
    conn.commit()
    conn.close()
    return True, normalized

def get_partner_id(user_id):
    """Return partner user_id if linked, otherwise None."""
    conn = _get_conn()
    cursor = conn.cursor()

    cursor.execute(
        '''
        SELECT CASE WHEN user_a = ? THEN user_b ELSE user_a END AS partner_id
        FROM partners
        WHERE user_a = ? OR user_b = ?
        LIMIT 1
        ''',
        (user_id, user_id, user_id),
    )
    row = cursor.fetchone()
    conn.close()
    return row['partner_id'] if row else None

def get_username(user_id):
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute('SELECT username FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return row['username']

def add_expense(user_id, amount, description, category, is_shared=False, paid_by=None):
    """Add an expense to the database."""
    conn = _get_conn()
    cursor = conn.cursor()

    cursor.execute(
        '''
        INSERT INTO expenses (user_id, amount, description, category, paid_by, is_shared)
        VALUES (?, ?, ?, ?, ?, ?)
        ''',
        (user_id, amount, description, category, paid_by or 'unknown', 1 if is_shared else 0),
    )

    expense_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return expense_id

def get_user_balance(user_id, week_start=None):
    """Return weekly personal totals and shared 50/50 settlement math for a linked couple."""
    partner_id = get_partner_id(user_id)
    week_start_value, week_end_value, week_date = _week_bounds(week_start)

    conn = _get_conn()
    cursor = conn.cursor()

    cursor.execute(
        'SELECT COALESCE(SUM(amount), 0) AS total FROM expenses WHERE user_id = ? AND is_shared = 0 AND created_at >= ? AND created_at < ?',
        (user_id, week_start_value, week_end_value),
    )
    personal_total = float(cursor.fetchone()['total'])

    if partner_id is None:
        conn.close()
        return {
            'week_start': week_date.isoformat(),
            'personal_total': personal_total,
            'shared_paid': 0.0,
            'shared_owed': 0.0,
            'shared_balance': 0.0,
            'overall_total': personal_total,
            'partner_username': None,
        }

    cursor.execute(
        'SELECT COALESCE(SUM(amount), 0) AS total FROM expenses WHERE user_id = ? AND is_shared = 1 AND created_at >= ? AND created_at < ?',
        (user_id, week_start_value, week_end_value),
    )
    shared_paid = float(cursor.fetchone()['total'])

    cursor.execute(
        'SELECT COALESCE(SUM(amount), 0) AS total FROM expenses WHERE user_id = ? AND is_shared = 1 AND created_at >= ? AND created_at < ?',
        (partner_id, week_start_value, week_end_value),
    )
    partner_shared_paid = float(cursor.fetchone()['total'])

    shared_pool = shared_paid + partner_shared_paid
    shared_owed = shared_pool / 2.0
    shared_balance = shared_paid - shared_owed

    partner_username = get_username(partner_id)
    conn.close()

    return {
        'week_start': week_date.isoformat(),
        'personal_total': personal_total,
        'shared_paid': shared_paid,
        'shared_owed': shared_owed,
        'shared_balance': shared_balance,
        'overall_total': personal_total + shared_owed,
        'partner_username': partner_username,
    }

def get_all_expenses(user_id, week_start=None):
    """Get weekly expenses for the user and, if linked, their partner too."""
    partner_id = get_partner_id(user_id)
    week_start_value, week_end_value, week_date = _week_bounds(week_start)

    conn = _get_conn()
    cursor = conn.cursor()

    if partner_id is None:
        cursor.execute(
            '''
            SELECT id, user_id, amount, description, category, paid_by, is_shared, created_at
            FROM expenses
            WHERE user_id = ? AND created_at >= ? AND created_at < ?
            ORDER BY created_at DESC
            ''',
            (user_id, week_start_value, week_end_value),
        )
    else:
        cursor.execute(
            '''
            SELECT id, user_id, amount, description, category, paid_by, is_shared, created_at
            FROM expenses
            WHERE user_id IN (?, ?) AND created_at >= ? AND created_at < ?
            ORDER BY created_at DESC
            ''',
            (user_id, partner_id, week_start_value, week_end_value),
        )

    rows = cursor.fetchall()
    conn.close()
    return rows

def get_expense_weeks(user_id, limit=8):
    """Return available week starts for the user and partner, newest first."""
    partner_id = get_partner_id(user_id)

    conn = _get_conn()
    cursor = conn.cursor()

    if partner_id is None:
        cursor.execute(
            '''
            SELECT created_at
            FROM expenses
            WHERE user_id = ?
            ORDER BY created_at DESC
            ''',
            (user_id,),
        )
    else:
        cursor.execute(
            '''
            SELECT created_at
            FROM expenses
            WHERE user_id IN (?, ?)
            ORDER BY created_at DESC
            ''',
            (user_id, partner_id),
        )

    rows = cursor.fetchall()
    conn.close()

    seen = []
    for row in rows:
        week_start = _expense_week_start(row['created_at'])
        if week_start not in seen:
            seen.append(week_start)
        if len(seen) >= limit:
            break

    return seen

def get_user_expenses(user_id, limit=10):
    """Get only the current user's own expenses (for editing/deleting)."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        '''
        SELECT id, user_id, amount, description, category, paid_by, is_shared, created_at
        FROM expenses
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        ''',
        (user_id, limit),
    )
    rows = cursor.fetchall()
    conn.close()
    return rows

def delete_expense(expense_id, user_id):
    """Delete an expense. Only succeeds if it belongs to user_id."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        'DELETE FROM expenses WHERE id = ? AND user_id = ?',
        (expense_id, user_id),
    )
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted

def update_expense(expense_id, user_id, amount=None, description=None, category=None):
    """Update fields of an expense. Only succeeds if it belongs to user_id."""
    conn = _get_conn()
    cursor = conn.cursor()

    updates = []
    values = []
    if amount is not None:
        updates.append('amount = ?')
        values.append(amount)
    if description is not None:
        updates.append('description = ?')
        values.append(description)
    if category is not None:
        updates.append('category = ?')
        values.append(category)

    if not updates:
        conn.close()
        return False

    values.extend([expense_id, user_id])
    cursor.execute(
        f"UPDATE expenses SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
        values,
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated
