import threading
import time
from collections import defaultdict
from datetime import datetime, timezone

from .. import db
from .errors import RateLimitError


FACEBOOK_GRAPHQL_DAILY_LIMIT = 2000
FACEBOOK_GRAPHQL_MIN_INTERVAL_SECONDS = 4

_account_locks = defaultdict(threading.Lock)


def local_usage_date():
    return datetime.now().date().isoformat()


def utc_now_text():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_utc(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def wait_for_facebook_graphql_slot(account_id):
    if not account_id:
        raise RateLimitError("请先选择抓取浏览器账号", "Missing browser_account_id for Facebook GraphQL request")

    lock = _account_locks[int(account_id)]
    lock.acquire()
    usage_date = local_usage_date()
    usage = db.get_graphql_usage(account_id, usage_date)
    if usage["request_count"] >= FACEBOOK_GRAPHQL_DAILY_LIMIT:
        lock.release()
        raise RateLimitError(
            "今日 Facebook 接口请求次数已达上限",
            f"Account {account_id} reached daily GraphQL limit: {usage['request_count']}/{FACEBOOK_GRAPHQL_DAILY_LIMIT}",
        )

    last_request_at = parse_utc(usage.get("last_request_at"))
    if last_request_at:
        elapsed = (datetime.now(timezone.utc) - last_request_at).total_seconds()
        wait_seconds = FACEBOOK_GRAPHQL_MIN_INTERVAL_SECONDS - elapsed
        if wait_seconds > 0:
            time.sleep(wait_seconds)
    return lock, usage_date


def record_facebook_graphql_request(account_id, usage_date):
    db.record_graphql_request(account_id, usage_date, utc_now_text())
