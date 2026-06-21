"""配置、常量与工具函数"""

import os
import sys
import json
import time
import warnings
import random
import re
import math
import urllib.request
import urllib.error
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

warnings.filterwarnings("ignore", message=".*HTTP_422_UNPROCESSABLE_ENTITY.*")

sys.path.insert(0, str(Path(__file__).parent))


def _try_import(name: str):
    try:
        if name == "mootdx.quotes":
            from mootdx.quotes import Quotes
            return Quotes
        elif name == "gradio":
            import gradio as gr
            return gr
        elif name == "openai":
            from openai import OpenAI
            return OpenAI
        return None
    except Exception:
        return None


Quotes = _try_import("mootdx.quotes")
gr = _try_import("gradio")
OpenAI = _try_import("openai")

DATA_ROOT = Path(__file__).parent / "data"
CONFIG_PATH = Path(__file__).parent / "config.json"
MA_PERIODS = [5, 10, 20]


def load_config() -> dict:
    try:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_config(data: dict):
    existing = load_config()
    existing.update(data)
    CONFIG_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2),
                           encoding="utf-8")


DATE_RANGE = {
    "daily": {"start": "2024-11-01", "label": "日K线(2024-11-01至今)"},
    "min15": {"months": 3, "label": "15分钟K线(最近3个月)"},
    "min1":  {"days": 3, "label": "1分钟K线(最近3天)"},
}

OFFSET = {"daily": 800, "min15": 3000, "min1": 5000}
OFFSET_INCR = {"daily": 60, "min15": 1000, "min1": 3000}

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
EM_SESSION = requests.Session()
EM_SESSION.headers.update({"User-Agent": UA})
EM_MIN_INTERVAL = 1.0
_em_last_call = [0.0]


def _load_index_codes() -> set:
    p = DATA_ROOT / "indices" / "index_codes.json"
    if p.exists():
        try:
            return set(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    return set()


INDEX_CODES = _load_index_codes()


def em_get(url: str, params: dict = None, headers: dict = None,
           timeout: int = 15, **kwargs):
    wait = EM_MIN_INTERVAL - (time.time() - _em_last_call[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.1, 0.5))
    hdrs = {"User-Agent": UA}
    if headers:
        hdrs.update(headers)
    try:
        return EM_SESSION.get(url, params=params, headers=hdrs,
                              timeout=timeout, **kwargs)
    finally:
        _em_last_call[0] = time.time()


# ── 工具函数 ──

def normalize_code(code: str) -> str:
    c = code.strip().upper()
    c = re.sub(r'^(SH|SZ|BJ)', '', c)
    c = re.sub(r'\.(SH|SZ|BJ)$', '', c)
    return c


def get_market(code: str, atype: str = None) -> str:
    c = code.strip()
    if atype == "stock":
        return "sh" if c.startswith(("6", "9")) else "bj" if c.startswith("8") else "sz"
    if atype == "etf":
        return "sh" if c.startswith(("51", "56", "588")) else "sz"
    if atype == "index":
        return "sh" if c.startswith("000") else "sz"
    if c in INDEX_CODES:
        return "sh" if c.startswith("000") else "sz"
    if c.startswith(("51", "56", "588")):
        return "sh"
    if c.startswith("159"):
        return "sz"
    if c.startswith(("6", "9")):
        return "sh"
    elif c.startswith("8"):
        return "bj"
    else:
        return "sz"


def get_mootdx_market(code: str) -> int:
    c = code.strip()
    if c in INDEX_CODES:
        return 1 if c.startswith("000") else 0
    if c.startswith(("51", "56", "588")):
        return 1
    if c.startswith("159"):
        return 0
    return 1 if c.startswith(("6", "9")) else 0


def detect_asset_type(code: str) -> str:
    if code in INDEX_CODES or (code.startswith("399") and len(code) == 6):
        return "index"
    if code.startswith(("51", "56", "159", "588", "16")):
        return "etf"
    return "stock"


_TYPE_DIR_MAP = {
    "stock": "stocks",
    "index": "indices",
    "etf": "etfs",
}


def get_data_dir(code: str, atype: str = None) -> Path:
    sub_dir = _TYPE_DIR_MAP.get(atype or detect_asset_type(code), "stocks")
    return DATA_ROOT / sub_dir / code


def get_ktype_category(ktype: str) -> int:
    return {"daily": 4, "min15": 9, "min1": 7}[ktype]


def get_ktype_col_date(ktype: str) -> str:
    return "date" if ktype == "daily" else "datetime"


def is_today_trading_day() -> bool:
    return datetime.now().weekday() < 5


def fmt_time(ts) -> str:
    if isinstance(ts, str):
        return ts
    if isinstance(ts, (datetime, date)):
        return ts.strftime("%Y-%m-%d %H:%M" if hasattr(ts, "hour") else "%Y-%m-%d")
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    return str(ts)


def date_range_check(ktype: str, ref_date_str: str) -> bool:
    config = DATE_RANGE[ktype]
    if ktype == "daily":
        return ref_date_str >= config["start"]
    elif ktype == "min15":
        cutoff = datetime.now() - timedelta(days=config["months"] * 30)
        return ref_date_str[:10] >= cutoff.strftime("%Y-%m-%d")
    elif ktype == "min1":
        cutoff = datetime.now() - timedelta(days=config["days"])
        return ref_date_str[:10] >= cutoff.strftime("%Y-%m-%d")
    return True
