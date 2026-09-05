import json
import re
import time
import traceback
from collections import defaultdict
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from . import db
from .fields import FIELD_LABELS, assign_default_write_columns, field_write_pairs, normalize_column
from .services.errors import InvalidLinkError, UserVisibleError
from .services.rate_limit import FACEBOOK_GRAPHQL_DAILY_LIMIT, local_usage_date
from .services.scraper import FacebookScraper, close_driver, ensure_logged_in, make_driver, now_text, page_state
from .services.sheets import SheetsClient, extract_spreadsheet_id


RUNNING = {}
STOP_REQUESTS = set()
PROFILE_LOCKS = defaultdict(lambda: False)
MAX_RUN_LOGS = 400


def short_url(url, limit=80):
    text = str(url or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def append_run_log(run_id, message, status="info", row_number=None, url=""):
    state = RUNNING.setdefault(int(run_id), {})
    logs = state.setdefault("logs", [])
    entry = {
        "time": now_text(),
        "status": status,
        "row_number": row_number,
        "url": url or "",
        "message": message,
    }
    logs.append(entry)
    if len(logs) > MAX_RUN_LOGS:
        del logs[:-MAX_RUN_LOGS]
    state["message"] = message
    if row_number is not None:
        state["current_row"] = row_number
        state["current_url"] = url or ""
    return entry


def note_progress(run_id, counts, message, status="info", row_number=None, url=""):
    append_run_log(run_id, message, status=status, row_number=row_number, url=url)
    if int(run_id) in RUNNING:
        RUNNING[int(run_id)]["counts"] = {
            "total": counts.get("total", 0),
            "success": counts.get("success", 0),
            "failed": counts.get("failed", 0),
            "skipped": counts.get("skipped", 0),
        }
    try:
        db.update_task_run_progress(run_id, counts)
    except Exception:
        pass


def enabled_fields(project):
    fields = [field for field in project["fields"] if field["enabled"]]
    return assign_default_write_columns(fields, project.get("write_start_column") or "B")


def browser_accounts_for_project(project):
    accounts = project.get("browser_accounts") or []
    if accounts:
        return accounts
    account = project.get("browser_account")
    return [account] if account else []


def available_browser_accounts(project):
    accounts = browser_accounts_for_project(project)
    if not accounts:
        return []
    usage_date = local_usage_date()
    available = []
    for account in accounts:
        usage = db.get_graphql_usage(account["id"], usage_date)
        if int(usage.get("request_count") or 0) < FACEBOOK_GRAPHQL_DAILY_LIMIT:
            available.append(account)
    return available or accounts


def project_for_account(project, index):
    accounts = available_browser_accounts(project)
    if not accounts:
        return project
    account = accounts[index % len(accounts)]
    selected = dict(project)
    selected["browser_account_id"] = account["id"]
    selected["browser_account"] = account
    return selected


def profile_keys(project):
    account_ids = [account["id"] for account in browser_accounts_for_project(project)]
    return [f"browser_account|{account_id}" for account_id in sorted(set(account_ids))] or ["browser_account|missing"]


def status_column(fields):
    for field in fields:
        if field["field_key"] == "status":
            return normalize_column(field.get("write_column"))
    return ""


def write_field_row(sheets, spreadsheet_id, worksheet_name, row_number, fields, values_map):
    pairs = field_write_pairs(fields, values_map)
    if pairs:
        sheets.write_mapped_values(spreadsheet_id, worksheet_name, row_number, pairs)
    return {field["field_key"]: values_map.get(field["field_key"], "") for field in fields}


def write_field_headers(sheets, spreadsheet_id, worksheet_name, header_row, fields):
    pairs = [
        (
            normalize_column(field.get("write_column")),
            field.get("field_label") or FIELD_LABELS.get(field["field_key"], field["field_key"]),
        )
        for field in fields
        if normalize_column(field.get("write_column"))
    ]
    if pairs:
        sheets.write_mapped_values(spreadsheet_id, worksheet_name, header_row, pairs)


def local_datetime_to_unix(value):
    if not value:
        return 0
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return int(dt.timestamp())


def checkpoint_matches(checkpoint, after_time, before_time):
    return (
        isinstance(checkpoint, dict)
        and int(checkpoint.get("afterTime") or 0) == int(after_time)
        and int(checkpoint.get("beforeTime") or 0) == int(before_time)
    )


def load_checkpoint(text, after_time, before_time):
    if not text:
        return {}
    try:
        checkpoint = json.loads(text)
    except Exception:
        return {}
    return checkpoint if checkpoint_matches(checkpoint, after_time, before_time) else {}


def make_checkpoint(cursor, has_next_page, after_time, before_time, counts, last_post_time=""):
    return {
        "cursor": cursor or "",
        "has_next_page": bool(has_next_page),
        "afterTime": int(after_time),
        "beforeTime": int(before_time),
        "processed_count": counts.get("total", 0),
        "success_count": counts.get("success", 0),
        "failed_count": counts.get("failed", 0),
        "last_post_time": last_post_time or "",
        "stopped_at": now_text(),
    }


def extract_profile_id(url, html=""):
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    if params.get("id"):
        return params["id"][0]
    source = f"{url}\n{html}"
    patterns = [
        r'"profile_id"\s*:\s*"(\d+)"',
        r'"pageID"\s*:\s*"(\d+)"',
        r'"page_id"\s*:\s*"(\d+)"',
        r'"owning_profile"\s*:\s*\{[^{}]*"id"\s*:\s*"(\d+)"',
        r'"id"\s*:\s*"(\d{8,})"',
    ]
    for pattern in patterns:
        match = re.search(pattern, source)
        if match:
            return match.group(1)
    return ""


def request_stop(run_id):
    STOP_REQUESTS.add(int(run_id))
    db.update_task_run_status(run_id, "stopping")
    if run_id in RUNNING:
        RUNNING[run_id]["status"] = "stopping"
        RUNNING[run_id]["message"] = "正在停止，等待当前贴文处理完成"


def should_stop(run_id):
    return int(run_id) in STOP_REQUESTS


def run_project(project_id, task_id=None, policy_override="", run_id=None):
    project = db.get_project(project_id)
    if not project:
        raise ValueError(f"Project not found: {project_id}")
    if run_id is None:
        run_id = db.create_task_run(project_id, task_id)
    counts = {"total": 0, "success": 0, "failed": 0, "skipped": 0}
    RUNNING[run_id] = {
        "status": "running",
        "project_id": project_id,
        "project_name": project.get("name") or "",
        "task_id": task_id,
        "message": "准备运行",
        "counts": dict(counts),
        "logs": [],
        "current_row": None,
        "current_url": "",
    }
    append_run_log(run_id, f"开始运行项目：{project.get('name') or project_id}")
    lock_keys = profile_keys(project)
    while any(PROFILE_LOCKS[key] for key in lock_keys):
        if should_stop(run_id):
            db.finish_task_run(run_id, "stopped", counts, "用户停止")
            note_progress(run_id, counts, "已停止：等待账号时被取消", status="stopped")
            RUNNING[run_id]["status"] = "stopped"
            STOP_REQUESTS.discard(run_id)
            return run_id
        note_progress(run_id, counts, "等待同一个抓取浏览器账号的其他任务完成")
        time.sleep(2)
    for key in lock_keys:
        PROFILE_LOCKS[key] = True
    try:
        if project.get("project_type") == "page":
            run_page_project(project, run_id, task_id, counts)
        else:
            run_post_project(project, run_id, task_id, counts, policy_override)
        if should_stop(run_id):
            final_status = "stopped"
            message = "已停止"
            error_message = "用户停止"
        else:
            final_status = "success" if counts["failed"] == 0 else "finished_with_errors"
            message = "运行完成"
            error_message = ""
            db.set_resume_row(project["id"], 0)
        db.finish_task_run(run_id, final_status, counts, error_message)
        summary = f"{message}：抓取 {counts['total']}，成功 {counts['success']}，失败 {counts['failed']}，跳过 {counts['skipped']}"
        note_progress(run_id, counts, summary, status=final_status)
        RUNNING[run_id]["status"] = final_status
        RUNNING[run_id]["message"] = summary
        return run_id
    except UserVisibleError as exc:
        db.finish_task_run(run_id, "failed", counts, exc.user_message)
        add_task_level_failure(run_id, exc.user_message, exc.technical)
        note_progress(run_id, counts, f"运行失败：{exc.user_message}", status="failed")
        RUNNING[run_id]["status"] = "failed"
        return run_id
    except Exception as exc:
        db.finish_task_run(run_id, "failed", counts, str(exc))
        add_task_level_failure(run_id, "任务启动失败，请查看技术详情", "".join(traceback.format_exception(exc)))
        note_progress(run_id, counts, f"运行失败：{exc}", status="failed")
        RUNNING[run_id]["status"] = "failed"
        return run_id
    finally:
        STOP_REQUESTS.discard(run_id)
        for key in lock_keys:
            PROFILE_LOCKS[key] = False


def run_post_project(project, run_id, task_id, counts, policy_override=""):
    sheets = SheetsClient()
    scraper = FacebookScraper()
    spreadsheet_id = project.get("spreadsheet_id") or extract_spreadsheet_id(project.get("spreadsheet_url", ""))
    fields = enabled_fields(project)
    status_col = status_column(fields)
    if fields:
        write_field_headers(sheets, spreadsheet_id, project["worksheet_name"], project["header_row"], fields)
    rows = sheets.read_column(spreadsheet_id, project["worksheet_name"], project["link_column"], project["start_row"])
    policy = policy_override or project.get("rerun_policy") or "skip_done"
    processed_log_column = normalize_column(project.get("processed_log_column"))
    write_start_column = normalize_column(project.get("write_start_column"), "B")
    skip_existing_write_data = bool(int(project.get("skip_existing_write_data", 1) or 0))
    start_row = int(project.get("start_row") or 2)
    log_map = (
        sheets.read_column_values(spreadsheet_id, project["worksheet_name"], processed_log_column, start_row)
        if processed_log_column and policy != "overwrite"
        else {}
    )
    write_map = (
        sheets.read_column_values(spreadsheet_id, project["worksheet_name"], write_start_column, start_row)
        if skip_existing_write_data and write_start_column and policy != "overwrite"
        else {}
    )
    status_map = (
        sheets.read_column_values(spreadsheet_id, project["worksheet_name"], status_col, start_row)
        if policy in {"skip_done", "retry_failed"} and status_col
        else {}
    )
    resume_after = int(project.get("resume_after_row") or 0)
    if resume_after > 0:
        note_progress(run_id, counts, f"从断点继续：跳过第 {resume_after} 行及之前的行，从第 {resume_after + 1} 行开始")
    rotation_index = 0
    for row in rows:
        if should_stop(run_id):
            break
        url = row["url"]
        if not url:
            continue
        if resume_after and int(row["row_number"]) <= resume_after:
            continue
        if policy != "overwrite":
            skip_reason = existing_row_skip_reason(
                row["row_number"],
                processed_log_column,
                write_start_column,
                skip_existing_write_data,
                log_map,
                write_map,
            )
            if skip_reason:
                counts["skipped"] += 1
                record_skipped_row(run_id, row, skip_reason)
                note_progress(
                    run_id,
                    counts,
                    f"第 {row['row_number']} 行跳过：{skip_reason}",
                    status="skipped",
                    row_number=row["row_number"],
                    url=url,
                )
                db.set_resume_row(project["id"], row["row_number"])
                continue
        if policy in {"skip_done", "retry_failed"} and status_col:
            existing_status = str(status_map.get(row["row_number"]) or "").strip()
            if policy == "skip_done" and existing_status in {"成功", "DONE", "success"}:
                counts["skipped"] += 1
                record_skipped_row(run_id, row, "状态列显示已成功")
                note_progress(
                    run_id,
                    counts,
                    f"第 {row['row_number']} 行跳过：状态列显示已成功",
                    status="skipped",
                    row_number=row["row_number"],
                    url=url,
                )
                db.set_resume_row(project["id"], row["row_number"])
                continue
            if policy == "retry_failed" and existing_status not in {"失败", "ERROR", "failed"}:
                counts["skipped"] += 1
                record_skipped_row(run_id, row, "当前行不是失败状态")
                note_progress(
                    run_id,
                    counts,
                    f"第 {row['row_number']} 行跳过：当前行不是失败状态",
                    status="skipped",
                    row_number=row["row_number"],
                    url=url,
                )
                db.set_resume_row(project["id"], row["row_number"])
                continue
        counts["total"] += 1
        active_project = project_for_account(project, rotation_index)
        rotation_index += 1
        account_name = (active_project.get("browser_account") or {}).get("name") or "未选择账号"
        note_progress(
            run_id,
            counts,
            f"正在抓取第 {row['row_number']} 行（账号：{account_name}） {short_url(url)}",
            status="running",
            row_number=row["row_number"],
            url=url,
        )
        try:
            result = scraper.scrape(url, active_project)
            result["values"]["post_url"] = result["values"].get("post_url") or url
            result["written"] = write_field_row(
                sheets,
                spreadsheet_id,
                project["worksheet_name"],
                row["row_number"],
                fields,
                result["values"],
            )
            result.setdefault("raw", {})["browser_account_id"] = active_project.get("browser_account_id")
            result["raw"]["browser_account_name"] = account_name
            if processed_log_column:
                marker = f"已抓取 | {result.get('post_id') or result['values'].get('post_id') or ''} | {now_text()}"
                try:
                    sheets.write_cell(
                        spreadsheet_id,
                        project["worksheet_name"],
                        processed_log_column,
                        row["row_number"],
                        marker,
                    )
                    result["written"]["processed_log"] = marker
                    log_map[row["row_number"]] = marker
                except Exception as exc:
                    result["raw"]["processed_log_error"] = repr(exc)
            db.add_row_run(run_id, row["row_number"], url, result)
            counts["success"] += 1
            note_progress(
                run_id,
                counts,
                f"第 {row['row_number']} 行抓取成功（账号：{account_name}）",
                status="success",
                row_number=row["row_number"],
                url=url,
            )
            db.set_resume_row(project["id"], row["row_number"])
        except UserVisibleError as exc:
            write_post_failure(sheets, spreadsheet_id, project, fields, row, run_id, exc.user_message, exc.technical)
            counts["failed"] += 1
            note_progress(
                run_id,
                counts,
                f"第 {row['row_number']} 行失败：{exc.user_message}",
                status="failed",
                row_number=row["row_number"],
                url=url,
            )
            db.set_resume_row(project["id"], row["row_number"])
        except Exception as exc:
            user_error = "抓取失败，请查看运行记录"
            write_post_failure(sheets, spreadsheet_id, project, fields, row, run_id, user_error, "".join(traceback.format_exception(exc)))
            counts["failed"] += 1
            note_progress(
                run_id,
                counts,
                f"第 {row['row_number']} 行失败：{user_error}",
                status="failed",
                row_number=row["row_number"],
                url=url,
            )
            db.set_resume_row(project["id"], row["row_number"])


def existing_row_skip_reason(
    row_number,
    processed_log_column,
    write_start_column,
    skip_existing_write_data,
    log_map,
    write_map,
):
    if processed_log_column and str(log_map.get(row_number) or "").strip():
        return f"日志列 {processed_log_column} 已有记录"
    if skip_existing_write_data and write_start_column and str(write_map.get(row_number) or "").strip():
        return f"写入开始列 {write_start_column} 已有数据"
    return ""


def record_skipped_row(run_id, row, reason):
    db.add_row_run(
        run_id,
        row["row_number"],
        row["url"],
        {
            "status": "skipped",
            "error_message": reason,
            "technical_error": "",
            "started_at": now_text(),
            "finished_at": now_text(),
            "raw": {"skip_reason": reason},
        },
    )


def add_task_level_failure(run_id, user_error, technical):
    db.add_row_run(
        run_id,
        0,
        "",
        {
            "status": "failed",
            "error_message": user_error,
            "technical_error": technical or user_error,
            "started_at": now_text(),
            "finished_at": now_text(),
        },
    )


def write_post_failure(sheets, spreadsheet_id, project, fields, row, run_id, user_error, technical):
    values_map = {"status": "失败", "error_message": user_error, "scraped_at": now_text(), "post_url": row["url"]}
    written = write_field_row(sheets, spreadsheet_id, project["worksheet_name"], row["row_number"], fields, values_map)
    db.add_row_run(
        run_id,
        row["row_number"],
        row["url"],
        {
            "status": "failed",
            "error_message": user_error,
            "technical_error": technical,
            "started_at": now_text(),
            "finished_at": now_text(),
            "written": written,
        },
    )


def run_page_project(project, run_id, task_id, counts):
    sheets = SheetsClient()
    scraper = FacebookScraper()
    spreadsheet_id = project.get("spreadsheet_id") or extract_spreadsheet_id(project.get("spreadsheet_url", ""))
    source_sheet = project.get("page_source_worksheet_name") or project.get("worksheet_name")
    output_sheet = project.get("page_output_worksheet_name") or project.get("worksheet_name")
    after_time = local_datetime_to_unix(project.get("page_start_at"))
    before_time = local_datetime_to_unix(project.get("page_end_at"))
    if not after_time or not before_time:
        raise ValueError("专页项目需要设置开始日期时间和结束日期时间")

    fields = enabled_fields(project)
    if fields:
        write_field_headers(sheets, spreadsheet_id, output_sheet, project["header_row"], fields)
    output_anchor_column = normalize_column(
        (fields[0].get("write_column") if fields else "") or project.get("write_start_column"),
        "B",
    )
    next_output_row = None

    page_rows = sheets.read_range_rows(spreadsheet_id, source_sheet, "A", "C", 2)
    driver = None
    try:
        for page_index, page_row in enumerate(page_rows):
            if should_stop(run_id):
                break
            values = page_row["values"]
            page_url = values[0].strip() if values else ""
            if not page_url:
                continue
            row_number = page_row["row_number"]
            checkpoint_text = values[2].strip() if len(values) >= 3 else ""
            checkpoint = load_checkpoint(checkpoint_text, after_time, before_time)
            cursor = checkpoint.get("cursor") or None
            page_counts = {"total": 0, "success": 0, "failed": 0, "skipped": 0}
            last_post_time = checkpoint.get("last_post_time", "")
            sheets.write_cell(spreadsheet_id, source_sheet, "B", row_number, "处理中")
            try:
                active_project = project_for_account(project, page_index)
                account_name = (active_project.get("browser_account") or {}).get("name") or "未选择账号"
                note_progress(
                    run_id,
                    counts,
                    f"正在处理专页第 {row_number} 行（账号：{account_name}） {short_url(page_url)}",
                    status="running",
                    row_number=row_number,
                    url=page_url,
                )
                driver = make_driver(active_project)
                ensure_logged_in(driver)
                driver.get(page_url)
                time.sleep(4)
                html = driver.page_source or ""
                page_state(driver.current_url, driver.title or "", html)
                profile_id = extract_profile_id(driver.current_url, html)
                if not profile_id:
                    raise InvalidLinkError("无法识别专页ID", f"url={driver.current_url}")

                while True:
                    if should_stop(run_id):
                        write_page_checkpoint(sheets, spreadsheet_id, source_sheet, row_number, cursor, True, after_time, before_time, page_counts, last_post_time, stopped=True)
                        break
                    note_progress(
                        run_id,
                        counts,
                        f"正在处理专页第 {row_number} 行（账号：{account_name}）",
                        status="running",
                        row_number=row_number,
                        url=page_url,
                    )
                    request_cursor = cursor
                    stories, page_info = scraper.fetch_profile_timeline(driver, active_project, profile_id, after_time, before_time, request_cursor)
                    next_cursor = page_info.get("end_cursor") or request_cursor
                    has_next_page = bool(page_info.get("has_next_page"))
                    if not stories:
                        write_page_checkpoint(sheets, spreadsheet_id, source_sheet, row_number, next_cursor, has_next_page, after_time, before_time, page_counts, last_post_time)
                        break
                    for story_values in stories:
                        post_url = story_values.get("post_url") or ""
                        post_time_unix = story_values.get("creation_time_unix") or 0
                        if post_time_unix and int(post_time_unix) < int(after_time):
                            has_next_page = False
                            continue
                        if post_time_unix and int(post_time_unix) > int(before_time):
                            continue
                        if not post_url:
                            page_counts["failed"] += 1
                            counts["failed"] += 1
                            continue
                        counts["total"] += 1
                        page_counts["total"] += 1
                        last_post_time = story_values.get("post_time") or last_post_time
                        try:
                            result = scraper.enrich_prefetched(story_values, post_url, driver, active_project)
                            if next_output_row is None:
                                next_output_row = sheets.next_empty_row(
                                    spreadsheet_id,
                                    output_sheet,
                                    output_anchor_column,
                                    int(project.get("header_row") or 1) + 1,
                                )
                            output_row = next_output_row
                            next_output_row += 1
                            result["written"] = write_field_row(
                                sheets,
                                spreadsheet_id,
                                output_sheet,
                                output_row,
                                fields,
                                result["values"],
                            )
                            result.setdefault("raw", {})["browser_account_id"] = active_project.get("browser_account_id")
                            result["raw"]["browser_account_name"] = account_name
                            db.add_row_run(run_id, row_number, post_url, result)
                            counts["success"] += 1
                            page_counts["success"] += 1
                            note_progress(
                                run_id,
                                counts,
                                f"专页第 {row_number} 行贴文抓取成功 {short_url(post_url)}",
                                status="success",
                                row_number=row_number,
                                url=post_url,
                            )
                        except Exception as exc:
                            db.add_row_run(
                                run_id,
                                row_number,
                                post_url,
                                {
                                    "status": "failed",
                                    "error_message": "抓取贴文失败，请查看技术详情",
                                    "technical_error": "".join(traceback.format_exception(exc)),
                                    "started_at": now_text(),
                                    "finished_at": now_text(),
                                    "raw": {"page_url": page_url, "post_url": post_url},
                                },
                            )
                            counts["failed"] += 1
                            page_counts["failed"] += 1
                            note_progress(
                                run_id,
                                counts,
                                f"专页第 {row_number} 行贴文失败：抓取贴文失败 {short_url(post_url)}",
                                status="failed",
                                row_number=row_number,
                                url=post_url,
                            )
                        if should_stop(run_id):
                            write_page_checkpoint(sheets, spreadsheet_id, source_sheet, row_number, request_cursor, True, after_time, before_time, page_counts, last_post_time, stopped=True)
                            break
                    if should_stop(run_id) or not has_next_page:
                        write_page_checkpoint(
                            sheets,
                            spreadsheet_id,
                            source_sheet,
                            row_number,
                            request_cursor if should_stop(run_id) else next_cursor,
                            True if should_stop(run_id) else has_next_page,
                            after_time,
                            before_time,
                            page_counts,
                            last_post_time,
                            stopped=should_stop(run_id),
                        )
                        break
                    cursor = next_cursor
                    write_page_checkpoint(sheets, spreadsheet_id, source_sheet, row_number, cursor, has_next_page, after_time, before_time, page_counts, last_post_time)
                if not should_stop(run_id):
                    sheets.write_cell(
                        spreadsheet_id,
                        source_sheet,
                        "B",
                        row_number,
                        f"完成：新增 {page_counts['success']}，失败 {page_counts['failed']}",
                    )
            except UserVisibleError as exc:
                sheets.write_cell(spreadsheet_id, source_sheet, "B", row_number, f"失败：{exc.user_message}")
                counts["failed"] += 1
                note_progress(
                    run_id,
                    counts,
                    f"专页第 {row_number} 行失败：{exc.user_message}",
                    status="failed",
                    row_number=row_number,
                    url=page_url,
                )
                db.add_row_run(
                    run_id,
                    row_number,
                    page_url,
                    {
                        "status": "failed",
                        "error_message": exc.user_message,
                        "technical_error": exc.technical,
                        "started_at": now_text(),
                        "finished_at": now_text(),
                    },
                )
            except Exception as exc:
                sheets.write_cell(spreadsheet_id, source_sheet, "B", row_number, "失败：专页处理失败，请查看运行记录")
                counts["failed"] += 1
                note_progress(
                    run_id,
                    counts,
                    f"专页第 {row_number} 行失败：专页处理失败，请查看运行记录",
                    status="failed",
                    row_number=row_number,
                    url=page_url,
                )
                db.add_row_run(
                    run_id,
                    row_number,
                    page_url,
                    {
                        "status": "failed",
                        "error_message": "专页处理失败，请查看运行记录",
                        "technical_error": "".join(traceback.format_exception(exc)),
                        "started_at": now_text(),
                        "finished_at": now_text(),
                    },
                )
            finally:
                close_driver(driver)
                driver = None
    finally:
        close_driver(driver)


def write_page_checkpoint(sheets, spreadsheet_id, source_sheet, row_number, cursor, has_next_page, after_time, before_time, counts, last_post_time, stopped=False):
    checkpoint = make_checkpoint(cursor, has_next_page, after_time, before_time, counts, last_post_time)
    sheets.write_cell(spreadsheet_id, source_sheet, "C", row_number, json.dumps(checkpoint, ensure_ascii=False))
    if stopped:
        sheets.write_cell(
            spreadsheet_id,
            source_sheet,
            "B",
            row_number,
            f"已停止：新增 {counts.get('success', 0)}，失败 {counts.get('failed', 0)}，可继续",
        )


def running_status():
    return RUNNING
