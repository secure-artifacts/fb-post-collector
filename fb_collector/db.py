import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import DB_PATH
from .fields import DEFAULT_FIELDS, assign_default_write_columns, column_to_index, index_to_column, normalize_column


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                project_type TEXT NOT NULL DEFAULT 'post',
                browser_account_id INTEGER,
                browser_account_ids_json TEXT NOT NULL DEFAULT '[]',
                browser_type TEXT NOT NULL DEFAULT 'chrome',
                browser_profile_mode TEXT NOT NULL DEFAULT 'dedicated',
                browser_profile_path TEXT NOT NULL DEFAULT '',
                spreadsheet_url TEXT NOT NULL DEFAULT '',
                spreadsheet_id TEXT NOT NULL DEFAULT '',
                worksheet_name TEXT NOT NULL DEFAULT '',
                page_source_worksheet_name TEXT NOT NULL DEFAULT '',
                page_output_worksheet_name TEXT NOT NULL DEFAULT '',
                page_start_at TEXT NOT NULL DEFAULT '',
                page_end_at TEXT NOT NULL DEFAULT '',
                link_column TEXT NOT NULL DEFAULT 'A',
                header_row INTEGER NOT NULL DEFAULT 1,
                start_row INTEGER NOT NULL DEFAULT 2,
                write_start_column TEXT NOT NULL DEFAULT 'B',
                processed_log_column TEXT NOT NULL DEFAULT 'AK',
                skip_existing_write_data INTEGER NOT NULL DEFAULT 1,
                rerun_policy TEXT NOT NULL DEFAULT 'skip_done',
                ocr_languages TEXT NOT NULL DEFAULT 'por',
                whisper_language TEXT NOT NULL DEFAULT '',
                audio_min_like_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(browser_account_id) REFERENCES browser_accounts(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS browser_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                browser_type TEXT NOT NULL DEFAULT 'chrome',
                user_data_dir TEXT NOT NULL DEFAULT '',
                debug_enabled INTEGER NOT NULL DEFAULT 0,
                facebook_login_status TEXT NOT NULL DEFAULT 'unknown',
                last_checked_at TEXT,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS project_fields (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                field_key TEXT NOT NULL,
                field_label TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                write_column TEXT NOT NULL DEFAULT '',
                UNIQUE(project_id, field_key),
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                schedule_type TEXT NOT NULL DEFAULT 'manual',
                schedule_config_json TEXT NOT NULL DEFAULT '{}',
                next_run_at TEXT,
                last_run_at TEXT,
                run_policy_override TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS task_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER,
                project_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                total_rows INTEGER NOT NULL DEFAULT 0,
                success_rows INTEGER NOT NULL DEFAULT 0,
                failed_rows INTEGER NOT NULL DEFAULT 0,
                skipped_rows INTEGER NOT NULL DEFAULT 0,
                error_message TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE SET NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS row_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_run_id INTEGER NOT NULL,
                sheet_row_number INTEGER NOT NULL,
                post_url TEXT NOT NULL,
                post_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                user_error_message TEXT NOT NULL DEFAULT '',
                technical_error TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                finished_at TEXT,
                raw_result_json TEXT NOT NULL DEFAULT '{}',
                written_values_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(task_run_id) REFERENCES task_runs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS facebook_graphql_usage (
                account_id INTEGER NOT NULL,
                usage_date TEXT NOT NULL,
                request_count INTEGER NOT NULL DEFAULT 0,
                last_request_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (account_id, usage_date),
                FOREIGN KEY(account_id) REFERENCES browser_accounts(id) ON DELETE CASCADE
            );
            """
        )
        ensure_column(conn, "projects", "project_type", "TEXT NOT NULL DEFAULT 'post'")
        ensure_column(conn, "projects", "browser_account_id", "INTEGER")
        ensure_column(conn, "projects", "browser_account_ids_json", "TEXT NOT NULL DEFAULT '[]'")
        ensure_column(conn, "projects", "page_source_worksheet_name", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "projects", "page_output_worksheet_name", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "projects", "page_start_at", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "projects", "page_end_at", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "projects", "audio_min_like_count", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "projects", "processed_log_column", "TEXT NOT NULL DEFAULT 'AK'")
        ensure_column(conn, "projects", "skip_existing_write_data", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(conn, "project_fields", "write_column", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "browser_accounts", "debug_enabled", "INTEGER NOT NULL DEFAULT 0")
        ensure_missing_project_fields(conn)
        backfill_write_columns(conn)
        close_stale_runs(conn)


def ensure_column(conn, table, column, ddl):
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def close_stale_runs(conn):
    conn.execute(
        """
        UPDATE task_runs
        SET status = 'failed',
            finished_at = COALESCE(finished_at, ?),
            error_message = CASE
                WHEN error_message IS NULL OR error_message = '' THEN '软件重启，上次运行中断'
                ELSE error_message
            END
        WHERE status IN ('running', 'stopping')
        """,
        (utc_now(),),
    )


def dict_row(row):
    return dict(row) if row else None


def list_projects():
    with connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM projects ORDER BY id DESC")]


def get_project(project_id):
    with connect() as conn:
        project = dict_row(conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone())
        if not project:
            return None
        try:
            account_ids = [int(value) for value in json.loads(project.get("browser_account_ids_json") or "[]")]
        except (TypeError, ValueError, json.JSONDecodeError):
            account_ids = []
        if not account_ids and project.get("browser_account_id"):
            account_ids = [int(project["browser_account_id"])]
        accounts_by_id = {}
        if account_ids:
            placeholders = ",".join("?" for _ in account_ids)
            accounts_by_id = {
                row["id"]: dict(row)
                for row in conn.execute(
                    f"SELECT * FROM browser_accounts WHERE id IN ({placeholders})",
                    account_ids,
                )
            }
        project["browser_account_ids"] = [account_id for account_id in account_ids if account_id in accounts_by_id]
        project["browser_accounts"] = [accounts_by_id[account_id] for account_id in project["browser_account_ids"]]
        project["browser_account"] = project["browser_accounts"][0] if project["browser_accounts"] else None
        project["fields"] = assign_default_write_columns(
            [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM project_fields WHERE project_id = ? ORDER BY sort_order, id",
                    (project_id,),
                )
            ],
            project.get("write_start_column") or "B",
        )
        return project


def create_project(name="新项目", project_type="post"):
    now = utc_now()
    if project_type not in {"post", "page"}:
        project_type = "post"
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO projects (name, project_type, ocr_languages, created_at, updated_at) VALUES (?, ?, 'por', ?, ?)",
            (name, project_type, now, now),
        )
        project_id = cur.lastrowid
        seed_fields(conn, project_id)
        return project_id


def seed_fields(conn, project_id, write_start_column="B"):
    start = column_to_index(write_start_column) or column_to_index("B")
    offset = 0
    for idx, (key, label, enabled) in enumerate(DEFAULT_FIELDS):
        write_column = index_to_column(start + offset) if enabled else ""
        if enabled:
            offset += 1
        conn.execute(
            """
            INSERT OR IGNORE INTO project_fields
            (project_id, field_key, field_label, enabled, sort_order, write_column)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (project_id, key, label, 1 if enabled else 0, idx, write_column),
        )


def ensure_missing_project_fields(conn):
    project_ids = [row["id"] for row in conn.execute("SELECT id FROM projects")]
    for project_id in project_ids:
        existing = {
            row["field_key"]
            for row in conn.execute("SELECT field_key FROM project_fields WHERE project_id = ?", (project_id,))
        }
        max_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS max_order FROM project_fields WHERE project_id = ?",
            (project_id,),
        ).fetchone()["max_order"]
        for key, label, enabled in DEFAULT_FIELDS:
            if key not in existing:
                max_order += 1
                conn.execute(
                    """
                    INSERT INTO project_fields
                    (project_id, field_key, field_label, enabled, sort_order, write_column)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (project_id, key, label, 1 if enabled else 0, max_order, ""),
                )


def backfill_write_columns(conn):
    projects = conn.execute("SELECT id, write_start_column FROM projects").fetchall()
    for project in projects:
        fields = conn.execute(
            """
            SELECT id, enabled, write_column
            FROM project_fields
            WHERE project_id = ?
            ORDER BY sort_order, id
            """,
            (project["id"],),
        ).fetchall()
        start = column_to_index(project["write_start_column"] or "B") or column_to_index("B")
        offset = 0
        for field in fields:
            current = normalize_column(field["write_column"])
            if field["enabled"]:
                if not current:
                    conn.execute(
                        "UPDATE project_fields SET write_column = ? WHERE id = ?",
                        (index_to_column(start + offset), field["id"]),
                    )
                offset += 1


def update_project(project_id, data):
    allowed = [
        "name",
        "project_type",
        "browser_account_id",
        "browser_account_ids_json",
        "browser_type",
        "browser_profile_mode",
        "browser_profile_path",
        "spreadsheet_url",
        "spreadsheet_id",
        "worksheet_name",
        "page_source_worksheet_name",
        "page_output_worksheet_name",
        "page_start_at",
        "page_end_at",
        "link_column",
        "header_row",
        "start_row",
        "write_start_column",
        "processed_log_column",
        "skip_existing_write_data",
        "rerun_policy",
        "ocr_languages",
        "whisper_language",
        "audio_min_like_count",
    ]
    cols = [key for key in allowed if key in data]
    if not cols:
        return
    values = [data[key] for key in cols]
    values.extend([utc_now(), project_id])
    assignment = ", ".join(f"{key} = ?" for key in cols)
    with connect() as conn:
        conn.execute(f"UPDATE projects SET {assignment}, updated_at = ? WHERE id = ?", values)


def update_project_fields(project_id, fields):
    with connect() as conn:
        for idx, item in enumerate(fields):
            conn.execute(
                """
                UPDATE project_fields
                SET enabled = ?, sort_order = ?, field_label = ?, write_column = ?
                WHERE project_id = ? AND field_key = ?
                """,
                (
                    1 if item.get("enabled") else 0,
                    idx,
                    item.get("field_label") or item.get("label") or item.get("field_key"),
                    normalize_column(item.get("write_column")),
                    project_id,
                    item["field_key"],
                ),
            )


def delete_project(project_id):
    with connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


def list_browser_accounts():
    with connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM browser_accounts ORDER BY id DESC")]


def get_browser_account(account_id):
    with connect() as conn:
        return dict_row(conn.execute("SELECT * FROM browser_accounts WHERE id = ?", (account_id,)).fetchone())


def create_browser_account(name="抓取账号"):
    now = utc_now()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO browser_accounts
            (name, browser_type, user_data_dir, facebook_login_status, created_at, updated_at)
            VALUES (?, 'chrome', '', 'unknown', ?, ?)
            """,
            (name, now, now),
        )
        account_id = cur.lastrowid
        conn.execute(
            "UPDATE browser_accounts SET user_data_dir = ? WHERE id = ?",
            (default_browser_account_dir(account_id), account_id),
        )
        return account_id


def default_browser_account_dir(account_id):
    from .config import DATA_DIR

    return str(DATA_DIR / "browser_profiles" / "accounts" / f"account_{account_id}")


def update_browser_account(account_id, data):
    allowed = ["name", "browser_type", "user_data_dir", "debug_enabled", "facebook_login_status", "last_checked_at", "notes"]
    cols = [key for key in allowed if key in data]
    if not cols:
        return
    values = [data[key] for key in cols]
    values.extend([utc_now(), account_id])
    assignment = ", ".join(f"{key} = ?" for key in cols)
    with connect() as conn:
        conn.execute(f"UPDATE browser_accounts SET {assignment}, updated_at = ? WHERE id = ?", values)


def delete_browser_account(account_id):
    with connect() as conn:
        conn.execute("UPDATE projects SET browser_account_id = NULL WHERE browser_account_id = ?", (account_id,))
        conn.execute("DELETE FROM browser_accounts WHERE id = ?", (account_id,))


def list_tasks():
    with connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT tasks.*, projects.name AS project_name
                FROM tasks
                JOIN projects ON projects.id = tasks.project_id
                ORDER BY tasks.id DESC
                """
            )
        ]


def get_task(task_id):
    with connect() as conn:
        return dict_row(conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())


def create_task(data):
    now = utc_now()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO tasks
            (project_id, name, enabled, schedule_type, schedule_config_json, next_run_at,
             run_policy_override, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["project_id"],
                data.get("name") or "新任务",
                1 if data.get("enabled", True) else 0,
                data.get("schedule_type", "manual"),
                json.dumps(data.get("schedule_config", {}), ensure_ascii=False),
                data.get("next_run_at"),
                data.get("run_policy_override", ""),
                now,
                now,
            ),
        )
        return cur.lastrowid


def update_task(task_id, data):
    with connect() as conn:
        conn.execute(
            """
            UPDATE tasks
            SET project_id = ?, name = ?, enabled = ?, schedule_type = ?,
                schedule_config_json = ?, next_run_at = ?, run_policy_override = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                data["project_id"],
                data.get("name") or "任务",
                1 if data.get("enabled", True) else 0,
                data.get("schedule_type", "manual"),
                json.dumps(data.get("schedule_config", {}), ensure_ascii=False),
                data.get("next_run_at"),
                data.get("run_policy_override", ""),
                utc_now(),
                task_id,
            ),
        )


def delete_task(task_id):
    with connect() as conn:
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))


def create_task_run(project_id, task_id=None):
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO task_runs (task_id, project_id, status, started_at)
            VALUES (?, ?, 'running', ?)
            """,
            (task_id, project_id, utc_now()),
        )
        return cur.lastrowid


def finish_task_run(run_id, status, counts, error_message=""):
    with connect() as conn:
        conn.execute(
            """
            UPDATE task_runs
            SET status = ?, finished_at = ?, total_rows = ?, success_rows = ?,
                failed_rows = ?, skipped_rows = ?, error_message = ?
            WHERE id = ?
            """,
            (
                status,
                utc_now(),
                counts.get("total", 0),
                counts.get("success", 0),
                counts.get("failed", 0),
                counts.get("skipped", 0),
                error_message,
                run_id,
            ),
        )


def update_task_run_status(run_id, status, error_message=""):
    with connect() as conn:
        conn.execute(
            "UPDATE task_runs SET status = ?, error_message = ? WHERE id = ?",
            (status, error_message, run_id),
        )


def update_task_run_progress(run_id, counts):
    with connect() as conn:
        conn.execute(
            """
            UPDATE task_runs
            SET total_rows = ?, success_rows = ?, failed_rows = ?, skipped_rows = ?
            WHERE id = ?
            """,
            (
                counts.get("total", 0),
                counts.get("success", 0),
                counts.get("failed", 0),
                counts.get("skipped", 0),
                run_id,
            ),
        )


def get_run(run_id):
    with connect() as conn:
        return dict_row(conn.execute("SELECT * FROM task_runs WHERE id = ?", (run_id,)).fetchone())


def add_row_run(run_id, row_number, post_url, result):
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO row_runs
            (task_run_id, sheet_row_number, post_url, post_id, status,
             user_error_message, technical_error, started_at, finished_at,
             raw_result_json, written_values_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                row_number,
                post_url,
                result.get("post_id", ""),
                result.get("status", "unknown"),
                result.get("error_message", ""),
                result.get("technical_error", ""),
                result.get("started_at", utc_now()),
                result.get("finished_at", utc_now()),
                json.dumps(result.get("raw", {}), ensure_ascii=False),
                json.dumps(result.get("written", {}), ensure_ascii=False),
            ),
        )


def list_runs(limit=50):
    with connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT task_runs.*, projects.name AS project_name, tasks.name AS task_name
                FROM task_runs
                JOIN projects ON projects.id = task_runs.project_id
                LEFT JOIN tasks ON tasks.id = task_runs.task_id
                ORDER BY task_runs.id DESC
                LIMIT ?
                """,
                (limit,),
            )
        ]


def list_row_runs(run_id):
    with connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM row_runs WHERE task_run_id = ? ORDER BY sheet_row_number",
                (run_id,),
            )
        ]


def setting_get(key, default=""):
    with connect() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def setting_set(key, value):
    with connect() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_graphql_usage(account_id, usage_date):
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM facebook_graphql_usage
            WHERE account_id = ? AND usage_date = ?
            """,
            (account_id, usage_date),
        ).fetchone()
        if row:
            return dict(row)
        return {"account_id": account_id, "usage_date": usage_date, "request_count": 0, "last_request_at": ""}


def record_graphql_request(account_id, usage_date, requested_at):
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO facebook_graphql_usage (account_id, usage_date, request_count, last_request_at)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(account_id, usage_date) DO UPDATE SET
                request_count = request_count + 1,
                last_request_at = excluded.last_request_at
            """,
            (account_id, usage_date, requested_at),
        )
