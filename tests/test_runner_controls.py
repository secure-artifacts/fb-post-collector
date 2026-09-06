import threading
import time
import unittest

from fb_collector import runner
from fb_collector import db
from fb_collector.app import create_app, normalize_project_form


class FakeSheets:
    written_rows = []
    lock = threading.Lock()

    def read_column(self, *args):
        return [{"row_number": number, "url": f"https://fb.com/post/{number}"} for number in range(2, 9)]

    def read_column_values(self, *args):
        return {}

    def write_mapped_values(self, spreadsheet_id, worksheet_name, row_number, pairs):
        with self.lock:
            self.written_rows.append(row_number)

    def write_cell(self, *args):
        return None


class FakeScraper:
    lock = threading.Lock()
    first_started = threading.Event()
    active = 0
    max_active = 0
    started = []
    active_accounts = set()
    account_overlap = False

    @classmethod
    def reset(cls):
        cls.first_started = threading.Event()
        cls.active = 0
        cls.max_active = 0
        cls.started = []
        cls.active_accounts = set()
        cls.account_overlap = False

    def scrape(self, url, project):
        with self.lock:
            account_id = project.get("browser_account_id")
            if account_id in type(self).active_accounts:
                type(self).account_overlap = True
            type(self).active_accounts.add(account_id)
            type(self).active += 1
            type(self).max_active = max(type(self).max_active, type(self).active)
            type(self).started.append((url, account_id))
            type(self).first_started.set()
        time.sleep(0.08)
        with self.lock:
            type(self).active -= 1
            type(self).active_accounts.remove(account_id)
        return {"values": {"post_url": url}, "post_id": url.rsplit("/", 1)[-1], "raw": {}}


class RunnerControlsTests(unittest.TestCase):
    def setUp(self):
        self.originals = {
            "SheetsClient": runner.SheetsClient,
            "FacebookScraper": runner.FacebookScraper,
            "get_graphql_usage": runner.db.get_graphql_usage,
            "add_row_run": runner.db.add_row_run,
            "update_task_run_progress": runner.db.update_task_run_progress,
            "set_resume_row": runner.db.set_resume_row,
            "update_task_run_status": runner.db.update_task_run_status,
        }
        runner.SheetsClient = FakeSheets
        runner.FacebookScraper = FakeScraper
        runner.db.get_graphql_usage = lambda *args: {"request_count": 0}
        runner.db.add_row_run = lambda *args, **kwargs: None
        runner.db.update_task_run_progress = lambda *args, **kwargs: None
        runner.db.set_resume_row = lambda *args, **kwargs: None
        runner.db.update_task_run_status = lambda *args, **kwargs: None
        runner.STOP_REQUESTS.clear()
        runner.PAUSE_REQUESTS.clear()
        FakeSheets.written_rows = []
        FakeScraper.reset()

    def tearDown(self):
        runner.SheetsClient = self.originals["SheetsClient"]
        runner.FacebookScraper = self.originals["FacebookScraper"]
        runner.db.get_graphql_usage = self.originals["get_graphql_usage"]
        runner.db.add_row_run = self.originals["add_row_run"]
        runner.db.update_task_run_progress = self.originals["update_task_run_progress"]
        runner.db.set_resume_row = self.originals["set_resume_row"]
        runner.db.update_task_run_status = self.originals["update_task_run_status"]

    def project(self, end_row=5, max_workers=3):
        accounts = [{"id": number, "name": f"账号{number}"} for number in range(1, 4)]
        return {
            "id": 1,
            "spreadsheet_id": "sheet",
            "worksheet_name": "data",
            "link_column": "A",
            "header_row": 1,
            "start_row": 2,
            "end_row": end_row,
            "max_workers": max_workers,
            "fields": [],
            "rerun_policy": "overwrite",
            "processed_log_column": "",
            "write_start_column": "D",
            "skip_existing_write_data": 0,
            "resume_after_row": 0,
            "browser_accounts": accounts,
            "browser_account": accounts[0],
        }

    def test_range_and_three_account_concurrency(self):
        runner.RUNNING[101] = {"logs": []}
        counts = {"total": 0, "success": 0, "failed": 0, "skipped": 0}
        runner.run_post_project(self.project(), 101, None, counts)
        self.assertEqual(counts, {"total": 4, "success": 4, "failed": 0, "skipped": 0})
        self.assertEqual(sorted(int(url.rsplit("/", 1)[-1]) for url, _ in FakeScraper.started), [2, 3, 4, 5])
        self.assertEqual(FakeScraper.max_active, 3)
        self.assertEqual({account_id for _, account_id in FakeScraper.started}, {1, 2, 3})
        self.assertFalse(FakeScraper.account_overlap)

    def test_one_worker_still_rotates_all_selected_accounts(self):
        runner.RUNNING[103] = {"logs": []}
        counts = {"total": 0, "success": 0, "failed": 0, "skipped": 0}
        runner.run_post_project(self.project(8, 1), 103, None, counts)
        self.assertEqual([account_id for _, account_id in FakeScraper.started], [1, 2, 3, 1, 2, 3, 1])
        self.assertEqual(FakeScraper.max_active, 1)
        self.assertFalse(FakeScraper.account_overlap)

    def test_two_workers_rotate_three_accounts_without_account_overlap(self):
        runner.RUNNING[104] = {"logs": []}
        counts = {"total": 0, "success": 0, "failed": 0, "skipped": 0}
        runner.run_post_project(self.project(8, 2), 104, None, counts)
        self.assertEqual({account_id for _, account_id in FakeScraper.started}, {1, 2, 3})
        self.assertEqual(FakeScraper.max_active, 2)
        self.assertFalse(FakeScraper.account_overlap)

    def test_pause_blocks_new_rows_until_resume(self):
        runner.RUNNING[102] = {"logs": []}
        counts = {"total": 0, "success": 0, "failed": 0, "skipped": 0}
        thread = threading.Thread(target=runner.run_post_project, args=(self.project(8, 2), 102, None, counts))
        thread.start()
        self.assertTrue(FakeScraper.first_started.wait(2))
        runner.request_pause(102)
        time.sleep(0.25)
        started_while_paused = len(FakeScraper.started)
        time.sleep(0.2)
        self.assertEqual(len(FakeScraper.started), started_while_paused)
        self.assertTrue(thread.is_alive())
        runner.request_resume(102)
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(counts["success"], 7)
        self.assertEqual(FakeScraper.max_active, 2)

    def test_form_normalizes_range_and_worker_limit(self):
        result = normalize_project_form(
            {"project_type": "post", "start_row": "20", "end_row": "10", "max_workers": "9"},
            [],
        )
        self.assertEqual(result["start_row"], 20)
        self.assertEqual(result["end_row"], 20)
        self.assertEqual(result["max_workers"], 3)

    def test_project_page_and_pause_routes_are_available(self):
        db.init_db()
        project_id = db.create_project("区间并发测试")
        app = create_app()
        app.config["TESTING"] = True
        response = app.test_client().get(f"/projects/{project_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn('name="end_row"'.encode(), response.data)
        self.assertIn('name="max_workers"'.encode(), response.data)
        self.assertIn('value="swa"'.encode(), response.data)
        self.assertIn('value="fra"'.encode(), response.data)
        self.assertIn('value="Latin"'.encode(), response.data)
        rules = {rule.rule for rule in app.url_map.iter_rules()}
        self.assertIn("/runs/<int:run_id>/pause", rules)
        self.assertIn("/runs/<int:run_id>/resume", rules)


if __name__ == "__main__":
    unittest.main()
