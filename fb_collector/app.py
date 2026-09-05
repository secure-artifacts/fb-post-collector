import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

from . import db
from .config import APP_VERSION
from .fields import assign_default_write_columns, normalize_column
from .runner import request_stop, run_project, running_status
from .scheduler import SchedulerThread, next_run
from .services.browser_profiles import account_debug_port, chrome_executable
from .services.component_installer import installation_status, start_installation
from .services.environment import detect_tools
from .services.errors import LoginRequiredError, UserVisibleError
from .services.rate_limit import FACEBOOK_GRAPHQL_DAILY_LIMIT, local_usage_date
from .services.scraper import facebook_logged_in, login_check_driver
from .services.sheets import clear_google_oauth, extract_spreadsheet_id, google_auth_status, run_google_oauth


WHISPER_LANGUAGE_OPTIONS = [
    ("", "自动识别"),
    ("en", "英语"),
    ("zh", "中文"),
    ("de", "德语"),
    ("es", "西班牙语"),
    ("ru", "俄语"),
    ("ko", "韩语"),
    ("fr", "法语"),
    ("ja", "日语"),
    ("pt", "葡萄牙语"),
    ("tr", "土耳其语"),
    ("pl", "波兰语"),
    ("ca", "加泰罗尼亚语"),
    ("nl", "荷兰语"),
    ("ar", "阿拉伯语"),
    ("sv", "瑞典语"),
    ("it", "意大利语"),
    ("id", "印度尼西亚语"),
    ("hi", "印地语"),
    ("fi", "芬兰语"),
    ("vi", "越南语"),
    ("he", "希伯来语"),
    ("uk", "乌克兰语"),
    ("el", "希腊语"),
    ("ms", "马来语"),
    ("cs", "捷克语"),
    ("ro", "罗马尼亚语"),
    ("da", "丹麦语"),
    ("hu", "匈牙利语"),
    ("ta", "泰米尔语"),
    ("no", "挪威语"),
    ("th", "泰语"),
    ("ur", "乌尔都语"),
    ("hr", "克罗地亚语"),
    ("bg", "保加利亚语"),
    ("lt", "立陶宛语"),
    ("la", "拉丁语"),
    ("mi", "毛利语"),
    ("ml", "马拉雅拉姆语"),
    ("cy", "威尔士语"),
    ("sk", "斯洛伐克语"),
    ("te", "泰卢固语"),
    ("fa", "波斯语"),
    ("lv", "拉脱维亚语"),
    ("bn", "孟加拉语"),
    ("sr", "塞尔维亚语"),
    ("az", "阿塞拜疆语"),
    ("sl", "斯洛文尼亚语"),
    ("kn", "卡纳达语"),
    ("et", "爱沙尼亚语"),
    ("mk", "马其顿语"),
    ("br", "布列塔尼语"),
    ("eu", "巴斯克语"),
    ("is", "冰岛语"),
    ("hy", "亚美尼亚语"),
    ("ne", "尼泊尔语"),
    ("mn", "蒙古语"),
    ("bs", "波斯尼亚语"),
    ("kk", "哈萨克语"),
    ("sq", "阿尔巴尼亚语"),
    ("sw", "斯瓦希里语"),
    ("gl", "加利西亚语"),
    ("mr", "马拉地语"),
    ("pa", "旁遮普语"),
    ("si", "僧伽罗语"),
    ("km", "高棉语"),
    ("sn", "绍纳语"),
    ("yo", "约鲁巴语"),
    ("so", "索马里语"),
    ("af", "南非荷兰语"),
    ("oc", "奥克语"),
    ("ka", "格鲁吉亚语"),
    ("be", "白俄罗斯语"),
    ("tg", "塔吉克语"),
    ("sd", "信德语"),
    ("gu", "古吉拉特语"),
    ("am", "阿姆哈拉语"),
    ("yi", "意第绪语"),
    ("lo", "老挝语"),
    ("uz", "乌兹别克语"),
    ("fo", "法罗语"),
    ("ht", "海地克里奥尔语"),
    ("ps", "普什图语"),
    ("tk", "土库曼语"),
    ("nn", "新挪威语"),
    ("mt", "马耳他语"),
    ("sa", "梵语"),
    ("lb", "卢森堡语"),
    ("my", "缅甸语"),
    ("bo", "藏语"),
    ("tl", "他加禄语"),
    ("mg", "马达加斯加语"),
    ("as", "阿萨姆语"),
    ("tt", "鞑靼语"),
    ("haw", "夏威夷语"),
    ("ln", "林加拉语"),
    ("ha", "豪萨语"),
    ("ba", "巴什基尔语"),
    ("jw", "爪哇语"),
    ("su", "巽他语"),
    ("yue", "粤语"),
]
OCR_LANGUAGE_OPTIONS = [
    ("por", "葡萄牙语"),
    ("eng", "英文"),
    ("ara", "阿拉伯语"),
    ("chi_sim", "中文简体"),
]


def create_app():
    db.init_db()
    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False
    app.secret_key = "fb-post-collector-local-dev"

    @app.route("/")
    def index():
        return render_template("index.html", projects=db.list_projects(), tasks=db.list_tasks(), runs=db.list_runs(10))

    @app.route("/api/ping")
    def api_ping():
        return jsonify({"ok": True, "version": APP_VERSION})

    @app.route("/browser-accounts")
    def browser_accounts():
        return render_template("browser_accounts.html", accounts=browser_accounts_with_usage())

    @app.route("/browser-accounts/new", methods=["POST"])
    def browser_account_new():
        account_id = db.create_browser_account(request.form.get("name") or "抓取账号")
        flash("已创建抓取浏览器账号。请点击“打开登录”并在专用 Chrome 中登录 Facebook。", "success")
        return redirect(url_for("browser_accounts"))

    @app.route("/browser-accounts/<int:account_id>", methods=["POST"])
    def browser_account_update(account_id):
        data = {
            "name": request.form.get("name") or "抓取账号",
            "browser_type": request.form.get("browser_type") or "chrome",
            "user_data_dir": request.form.get("user_data_dir") or db.default_browser_account_dir(account_id),
            "debug_enabled": 1 if request.form.get("debug_enabled") == "on" else 0,
            "notes": request.form.get("notes") or "",
        }
        db.update_browser_account(account_id, data)
        flash("已保存浏览器账号。", "success")
        return redirect(url_for("browser_accounts"))

    @app.route("/browser-accounts/<int:account_id>/delete", methods=["POST"])
    def browser_account_delete(account_id):
        db.delete_browser_account(account_id)
        flash("已删除浏览器账号，已绑定该账号的项目会变为未绑定。", "success")
        return redirect(url_for("browser_accounts"))

    @app.route("/browser-accounts/<int:account_id>/open", methods=["POST"])
    def browser_account_open(account_id):
        account = db.get_browser_account(account_id)
        if not account:
            flash("浏览器账号不存在。", "danger")
            return redirect(url_for("browser_accounts"))
        try:
            open_browser_account(account, "https://www.facebook.com/")
            flash("已打开该账号的专用 Chrome。请在里面登录 Facebook，登录完成后可以直接点“检测登录”，不用先关窗口。", "success")
        except Exception as exc:
            flash(f"打开浏览器失败：{exc}", "danger")
        return redirect(url_for("browser_accounts"))

    @app.route("/browser-accounts/<int:account_id>/check", methods=["POST"])
    def browser_account_check(account_id):
        account = db.get_browser_account(account_id)
        if not account:
            if wants_json():
                return jsonify({"error": "浏览器账号不存在。"}), 404
            flash("浏览器账号不存在。", "danger")
            return redirect(url_for("browser_accounts"))
        status, message = check_facebook_login(account)
        checked_at = db.utc_now()
        db.update_browser_account(
            account_id,
            {
                "facebook_login_status": status,
                "last_checked_at": checked_at,
            },
        )
        if wants_json():
            return jsonify(
                {
                    "id": account_id,
                    "status": status,
                    "message": message,
                    "last_checked_at": checked_at,
                }
            )
        flash(message, "success" if status == "logged_in" else "warning")
        return redirect(url_for("browser_accounts"))

    @app.route("/projects/new", methods=["POST"])
    def project_new():
        project_id = db.create_project(
            request.form.get("name") or "新项目",
            request.form.get("project_type") or "post",
        )
        return redirect(url_for("project_edit", project_id=project_id))

    @app.route("/projects/<int:project_id>")
    def project_edit(project_id):
        project = db.get_project(project_id)
        if not project:
            return redirect(url_for("index"))
        return render_template(
            "project.html",
            project=project,
            browser_accounts=browser_accounts_with_usage(),
            whisper_language_options=WHISPER_LANGUAGE_OPTIONS,
            ocr_language_options=OCR_LANGUAGE_OPTIONS,
        )

    @app.route("/projects/<int:project_id>", methods=["POST"])
    def project_save(project_id):
        db.init_db()
        project = db.get_project(project_id)
        if not project:
            return redirect(url_for("index"))
        data = normalize_project_form(dict(request.form), request.form.getlist("browser_account_ids"))
        data["project_type"] = project.get("project_type") or "post"
        if data.get("spreadsheet_url"):
            data["spreadsheet_id"] = extract_spreadsheet_id(data["spreadsheet_url"])
        db.update_project(project_id, data)
        fields = json.loads(request.form.get("fields_json", "[]"))
        if data.get("project_type") == "page":
            for field in fields:
                if field.get("field_key") == "post_url":
                    field["enabled"] = True
        if fields:
            for field in fields:
                field["write_column"] = normalize_column(field.get("write_column"))
            assign_default_write_columns(fields, data.get("write_start_column") or "B")
            db.update_project_fields(project_id, fields)
        saved = db.get_project(project_id)
        if not saved:
            flash("项目保存失败，请重新打开项目后再试。", "danger")
        elif str(saved.get("audio_min_like_count") or 0) != str(data.get("audio_min_like_count") or 0):
            flash("项目已保存，但音频识别点赞门槛没有写入成功，请重启软件后再试。", "warning")
        elif not data.get("browser_account_id"):
            flash("项目配置已保存。运行前请先选择一个抓取浏览器账号。", "warning")
        else:
            flash("项目已保存。", "success")
        return redirect(url_for("project_edit", project_id=project_id))

    @app.route("/projects/<int:project_id>/delete", methods=["POST"])
    def project_delete(project_id):
        db.delete_project(project_id)
        return redirect(url_for("index"))

    @app.route("/projects/<int:project_id>/run", methods=["POST"])
    def project_run(project_id):
        if request.form.get("from_start") == "1":
            db.set_resume_row(project_id, 0)
        run_id = db.create_task_run(project_id, None)
        thread = threading.Thread(
            target=run_project,
            args=(project_id, None, request.form.get("policy", "")),
            kwargs={"run_id": run_id},
            daemon=True,
        )
        thread.start()
        return redirect(url_for("run_detail", run_id=run_id))

    @app.route("/tasks")
    def tasks():
        return render_template("tasks.html", tasks=db.list_tasks(), projects=db.list_projects())

    @app.route("/tasks/new", methods=["POST"])
    def task_new():
        data = task_form_data(request.form)
        db.create_task(data)
        return redirect(url_for("tasks"))

    @app.route("/tasks/<int:task_id>", methods=["POST"])
    def task_update(task_id):
        db.update_task(task_id, task_form_data(request.form))
        return redirect(url_for("tasks"))

    @app.route("/tasks/<int:task_id>/delete", methods=["POST"])
    def task_delete(task_id):
        db.delete_task(task_id)
        return redirect(url_for("tasks"))

    @app.route("/runs")
    def runs():
        return render_template("runs.html", runs=db.list_runs(100), running=running_status())

    @app.route("/runs/<int:run_id>")
    def run_detail(run_id):
        return render_template("run_detail.html", rows=db.list_row_runs(run_id), run_id=run_id, run=db.get_run(run_id))

    @app.route("/runs/clear-failed", methods=["POST"])
    def runs_clear_failed():
        deleted = db.delete_failed_runs()
        if wants_json():
            return jsonify({"deleted": deleted})
        flash(f"已清空 {deleted} 条失败运行记录。", "success")
        return redirect(url_for("runs"))

    @app.route("/runs/<int:run_id>/stop", methods=["POST"])
    def run_stop(run_id):
        run = db.get_run(run_id)
        if run and run.get("status") in {"running", "stopping"}:
            request_stop(run_id)
            flash("已请求停止，当前贴文处理完成后会停止。", "warning")
        return redirect(request.referrer or url_for("run_detail", run_id=run_id))

    @app.route("/environment")
    def environment():
        return render_template("environment.html")

    @app.route("/environment/data")
    def environment_data():
        return jsonify(
            {
                "tools": detect_tools(),
                "browser_accounts": db.list_browser_accounts(),
                "installation": installation_status(),
            }
        )

    @app.route("/environment/install", methods=["POST"])
    def environment_install():
        payload = request.get_json(silent=True) or {}
        try:
            state = start_installation(payload.get("components") or [])
            return jsonify(state), 202
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"error": str(exc), "installation": installation_status()}), 409

    @app.route("/environment/install/status")
    def environment_install_status():
        return jsonify(installation_status())

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        if request.method == "POST":
            db.setting_set("gyazo_access_token", request.form.get("gyazo_access_token", ""))
            return redirect(url_for("settings"))
        return render_template(
            "settings.html",
            gyazo_access_token=db.setting_get("gyazo_access_token"),
            google_auth=google_auth_status(),
        )

    @app.context_processor
    def template_helpers():
        def status_label(status):
            return {
                "running": "运行中",
                "stopping": "正在停止",
                "stopped": "已停止",
                "success": "成功",
                "finished_with_errors": "有错误",
                "failed": "失败",
                "skipped": "已跳过",
            }.get(status or "", status or "")

        def status_badge_class(status):
            return {
                "running": "text-bg-primary",
                "stopping": "text-bg-warning",
                "stopped": "text-bg-secondary",
                "success": "text-bg-success",
                "finished_with_errors": "text-bg-danger",
                "failed": "text-bg-danger",
                "skipped": "text-bg-secondary",
            }.get(status or "", "text-bg-secondary")

        return {
            "status_label": status_label,
            "status_badge_class": status_badge_class,
            "app_version": APP_VERSION,
        }

    @app.route("/auth/google/login", methods=["POST"])
    def google_login():
        try:
            run_google_oauth()
            flash("Google 授权成功。", "success")
        except Exception as exc:
            flash(f"Google 授权失败：{exc}", "danger")
        return redirect(url_for("settings"))

    @app.route("/auth/google/logout", methods=["POST"])
    def google_logout():
        clear_google_oauth()
        flash("已删除 Google 授权，可以重新登录。", "success")
        return redirect(url_for("settings"))

    @app.route("/api/status")
    def api_status():
        return jsonify({"version": APP_VERSION, "running": running_status(), "runs": db.list_runs(10)})

    @app.route("/api/runs/live")
    def api_runs_live():
        return jsonify(live_runs_payload())

    @app.route("/api/runs/<int:run_id>/live")
    def api_run_live(run_id):
        return jsonify(live_run_detail(run_id))

    return app


def logs_from_row_runs(rows):
    logs = []
    for row in rows:
        status = row.get("status") or "info"
        number = row.get("sheet_row_number")
        url = row.get("post_url") or ""
        error = row.get("user_error_message") or ""
        if status == "success":
            message = f"第 {number} 行抓取成功"
        elif status == "skipped":
            message = f"第 {number} 行跳过：{error}" if error else f"第 {number} 行已跳过"
        elif status == "failed":
            message = f"第 {number} 行失败：{error}" if error else f"第 {number} 行失败"
        else:
            message = error or f"第 {number} 行 {status}"
        logs.append(
            {
                "time": row.get("finished_at") or row.get("started_at") or "",
                "status": status,
                "row_number": number,
                "url": url,
                "message": message,
            }
        )
    return logs


def serialize_run_row(run):
    return {
        "id": run.get("id"),
        "project_name": run.get("project_name") or "",
        "task_name": run.get("task_name") or "手动",
        "status": run.get("status") or "",
        "total_rows": run.get("total_rows") or 0,
        "success_rows": run.get("success_rows") or 0,
        "failed_rows": run.get("failed_rows") or 0,
        "skipped_rows": run.get("skipped_rows") or 0,
        "error_message": run.get("error_message") or "",
        "started_at": run.get("started_at") or "",
        "finished_at": run.get("finished_at") or "",
    }


def live_run_detail(run_id):
    run = db.get_run(run_id)
    if not run:
        return {"error": "运行记录不存在"}
    state = running_status().get(int(run_id)) or {}
    rows = db.list_row_runs(run_id)
    logs = state.get("logs") or logs_from_row_runs(rows)
    counts = state.get("counts") or {
        "total": run.get("total_rows") or 0,
        "success": run.get("success_rows") or 0,
        "failed": run.get("failed_rows") or 0,
        "skipped": run.get("skipped_rows") or 0,
    }
    return {
        "id": run_id,
        "project_name": state.get("project_name") or "",
        "status": state.get("status") or run.get("status") or "",
        "message": state.get("message") or "",
        "current_row": state.get("current_row"),
        "current_url": state.get("current_url") or "",
        "counts": counts,
        "logs": logs[-200:],
        "rows": [
            {
                "sheet_row_number": row.get("sheet_row_number"),
                "post_url": row.get("post_url") or "",
                "status": row.get("status") or "",
                "user_error_message": row.get("user_error_message") or "",
            }
            for row in rows[-200:]
        ],
    }


def live_runs_payload():
    runs = [serialize_run_row(run) for run in db.list_runs(50)]
    live_states = running_status()
    active = None
    live_ids = {
        int(run_id)
        for run_id, state in live_states.items()
        if (state or {}).get("status") in {"running", "stopping"}
    }
    for run in runs:
        if int(run["id"]) in live_ids or (run["status"] in {"running", "stopping"} and int(run["id"]) in live_states):
            extra = live_run_detail(run["id"])
            extra["project_name"] = extra.get("project_name") or run["project_name"]
            active = extra
            run["total_rows"] = extra["counts"].get("total", run["total_rows"])
            run["success_rows"] = extra["counts"].get("success", run["success_rows"])
            run["failed_rows"] = extra["counts"].get("failed", run["failed_rows"])
            run["skipped_rows"] = extra["counts"].get("skipped", run["skipped_rows"])
            run["status"] = extra.get("status") or run["status"]
            break
    if not active:
        for run_id, state in live_states.items():
            if (state or {}).get("status") in {"running", "stopping"}:
                active = live_run_detail(int(run_id))
                break
    if not active and runs:
        active = live_run_detail(runs[0]["id"])
        active["project_name"] = active.get("project_name") or runs[0]["project_name"]
    return {"runs": runs, "active": active}


def open_browser_account(account, url):
    user_data_dir = Path(account["user_data_dir"])
    user_data_dir.mkdir(parents=True, exist_ok=True)
    port = account_debug_port(account["id"])
    subprocess.Popen(
        [
            chrome_executable(),
            f"--user-data-dir={user_data_dir}",
            f"--remote-debugging-port={port}",
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def browser_accounts_with_usage():
    usage_date = local_usage_date()
    accounts = db.list_browser_accounts()
    for account in accounts:
        usage = db.get_graphql_usage(account["id"], usage_date)
        used = int(usage.get("request_count") or 0)
        remaining = max(FACEBOOK_GRAPHQL_DAILY_LIMIT - used, 0)
        percent = min(round(used / FACEBOOK_GRAPHQL_DAILY_LIMIT * 100, 1), 100)
        account["graphql_usage"] = {
            "date": usage_date,
            "used": used,
            "limit": FACEBOOK_GRAPHQL_DAILY_LIMIT,
            "remaining": remaining,
            "percent": percent,
            "is_limited": used >= FACEBOOK_GRAPHQL_DAILY_LIMIT,
            "last_request_at": usage.get("last_request_at") or "",
        }
    return accounts


def wants_json():
    requested = (request.headers.get("X-Requested-With") or "").lower()
    if requested in {"fetch", "xmlhttprequest"}:
        return True
    best = request.accept_mimetypes.best_match(["application/json", "text/html"])
    return best == "application/json" and request.accept_mimetypes[best] >= request.accept_mimetypes["text/html"]


def check_facebook_login(account):
    driver = None
    attached = False
    try:
        driver, attached = login_check_driver(account)
        driver.get("https://www.facebook.com/")
        time.sleep(3)
        if facebook_logged_in(driver):
            return "logged_in", f"{account['name']} 已登录 Facebook。"
        return "not_logged_in", f"{account['name']} 尚未登录 Facebook。请点击“打开登录”，登录完成后可以直接点“检测登录”，不用先关窗口。"
    except LoginRequiredError:
        return "not_logged_in", f"{account['name']} 尚未登录 Facebook。请点击“打开登录”。"
    except UserVisibleError as exc:
        return "unknown", exc.user_message
    except Exception as exc:
        return "unknown", f"无法确认登录状态：{exc}"
    finally:
        if driver and not attached:
            try:
                driver.quit()
            except Exception:
                pass


def task_form_data(form):
    schedule_type = form.get("schedule_type", "manual")
    config = {
        "interval": int(form.get("interval") or 1),
        "hour": int(form.get("hour") or 9),
        "minute": int(form.get("minute") or 0),
        "weekday": int(form.get("weekday") or 0),
        "day": int(form.get("day") or 1),
    }
    next_at = form.get("next_run_at") or ""
    if not next_at and schedule_type != "manual":
        upcoming = next_run(schedule_type, config, datetime.now(timezone.utc))
        next_at = upcoming.isoformat() if upcoming else None
    return {
        "project_id": int(form["project_id"]),
        "name": form.get("name") or "任务",
        "enabled": form.get("enabled") == "on",
        "schedule_type": schedule_type,
        "schedule_config": config,
        "next_run_at": next_at,
        "run_policy_override": form.get("run_policy_override", ""),
    }


def normalize_project_form(data, account_ids=None):
    normalized_account_ids = []
    for value in account_ids or []:
        try:
            account_id = int(value)
        except (TypeError, ValueError):
            continue
        if account_id > 0 and account_id not in normalized_account_ids:
            normalized_account_ids.append(account_id)
    legacy_account_id = data.get("browser_account_id") or None
    if not normalized_account_ids and legacy_account_id:
        normalized_account_ids = [int(legacy_account_id)]
    data["browser_account_id"] = normalized_account_ids[0] if normalized_account_ids else None
    data["browser_account_ids_json"] = json.dumps(normalized_account_ids)
    data["project_type"] = data.get("project_type") or "post"
    data["browser_type"] = "chrome"
    data["browser_profile_mode"] = "browser_account"
    data["browser_profile_path"] = ""
    data["ocr_languages"] = normalize_ocr_language(data.get("ocr_languages"))
    data["audio_min_like_count"] = normalize_non_negative_int(data.get("audio_min_like_count"))
    data["write_start_column"] = normalize_column(data.get("write_start_column"), "B")
    data["processed_log_column"] = normalize_column(data.get("processed_log_column"), "")
    data["skip_existing_write_data"] = 1 if data.get("skip_existing_write_data") == "on" else 0
    if data["project_type"] == "page":
        data["worksheet_name"] = data.get("page_source_worksheet_name") or data.get("worksheet_name") or ""
        data["link_column"] = "A"
        data["start_row"] = 2
        data["rerun_policy"] = data.get("rerun_policy") or "overwrite"
    return data


def normalize_ocr_language(value):
    if value in {"eng", "por", "ara", "chi_sim"}:
        return value
    if value and "+" in value:
        return "por" if "por" in value.split("+") else value.split("+")[0]
    return "por"


def normalize_non_negative_int(value):
    try:
        return max(0, int(str(value or "0").strip()))
    except ValueError:
        return 0


def main():
    from .tray import _log, ensure_port_free, http_alive, pick_port, start_desktop_shell

    host = "127.0.0.1"
    _log(f"main start frozen={getattr(sys, 'frozen', False)} pid={os.getpid()}")
    port, existing = pick_port(host, 5199)
    url = f"http://{host}:{port}"
    _log(f"picked port={port} existing={existing}")
    if existing or http_alive(url + "/api/ping"):
        _log(f"already running, open {url}")
        webbrowser.open(url)
        return
    ensure_port_free(host, port)
    _log(f"port ready {port}")

    app = create_app()
    scheduler = SchedulerThread(interval=30)
    scheduler.start()

    def quit_app():
        scheduler.stop()
        os._exit(0)

    def start_server():
        app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)

    start_desktop_shell(url, start_server, quit_app)
