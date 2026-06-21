"""本地数据管理 + 获取调度 + 股票搜索"""

import json
import re
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from config import (
    DATA_ROOT, MA_PERIODS, _TYPE_DIR_MAP, detect_asset_type,
    get_data_dir, get_ktype_col_date, DATE_RANGE, normalize_code, UA, INDEX_CODES,
)
from sources import MootdxSource, TencentSource, SinaSource, EastMoneySource
from tools.indicators import add_ma_to_csv, calc_ma, ensure_ascending


def search_stock_by_name(keyword: str) -> list[dict]:
    """通过股票名称/拼音模糊搜索，返回匹配的代码列表。"""
    if not keyword or len(keyword.strip()) < 1:
        return []
    keyword = keyword.strip()
    if re.match(r'^\d{6}$', keyword):
        return [{"code": keyword, "name": "", "type": detect_asset_type(keyword)}]

    local_results = []
    for status in StorageManager.list_cached():
        name = (status.get("name", "") or "")
        code = status["code"]
        if keyword.upper() in name.upper() or keyword in code:
            local_results.append({
                "code": code,
                "name": name,
                "type": status.get("type", detect_asset_type(code)),
            })
    if local_results:
        return local_results

    try:
        import urllib.parse
        url = f"http://suggest3.sinajs.cn/suggest/type=&key={urllib.parse.quote(keyword)}"
        req = urllib.request.Request(url)
        req.add_header("User-Agent", UA)
        resp = urllib.request.urlopen(req, timeout=5)
        raw = resp.read().decode("gbk")
        match = re.search(r'=\s*\[(.+)\]', raw)
        if not match:
            return []
        items_str = match.group(1)
        results = []
        for item_match in re.finditer(r'\[(.*?)\]', items_str):
            parts = item_match.group(1).split(",")
            if len(parts) >= 3:
                full_code = parts[0].strip('"\' ')
                name = parts[1].strip('"\' ')
                code_match = re.search(r'(\d{6})', full_code)
                if code_match:
                    code = code_match.group(1)
                    t = "stock"
                    if code in INDEX_CODES:
                        t = "index"
                    elif code.startswith(("51", "159", "588")):
                        t = "etf"
                    results.append({"code": code, "name": name, "type": t})
        return results[:10]
    except Exception:
        return []


class StorageManager:
    """本地数据管理"""

    @staticmethod
    def ensure_dirs(code: str, atype: str = None) -> Path:
        d = get_data_dir(code, atype)
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def csv_path(code: str, ktype: str, atype: str = None) -> Path:
        names = {"daily": "daily", "min15": "min15", "min1": "min1"}
        return get_data_dir(code, atype) / f"{names[ktype]}.csv"

    @staticmethod
    def ma_path(code: str, atype: str = None) -> Path:
        return get_data_dir(code, atype) / "ma_indicators.csv"

    @staticmethod
    def info_path(code: str, atype: str = None) -> Path:
        return get_data_dir(code, atype) / "info.json"

    @staticmethod
    def metadata_path(code: str, atype: str = None) -> Path:
        return get_data_dir(code, atype) / "metadata.json"

    @staticmethod
    def exists(code: str, ktype: str) -> bool:
        return StorageManager.csv_path(code, ktype).exists()

    @staticmethod
    def load_csv(code: str, ktype: str, atype: str = None) -> pd.DataFrame:
        p = StorageManager.csv_path(code, ktype, atype)
        if not p.exists():
            return pd.DataFrame()
        df = pd.read_csv(p)
        date_col = get_ktype_col_date(ktype)
        if date_col in df.columns:
            df[date_col] = df[date_col].astype(str)
        return df

    @staticmethod
    def save_csv(code: str, ktype: str, df: pd.DataFrame, atype: str = None):
        p = StorageManager.csv_path(code, ktype, atype)
        p.parent.mkdir(parents=True, exist_ok=True)
        date_col = get_ktype_col_date(ktype)
        if date_col in df.columns:
            df = df.sort_values(by=date_col, ascending=False).reset_index(drop=True)
        df.to_csv(p, index=False, encoding="utf-8-sig")
        try:
            add_ma_to_csv(p, periods=MA_PERIODS)
        except Exception:
            pass

    @staticmethod
    def load_ma_data(code: str) -> pd.DataFrame:
        df = StorageManager.load_csv(code, "daily")
        if not df.empty:
            df = ensure_ascending(df)
            df = calc_ma(df, periods=MA_PERIODS)
        return df

    @staticmethod
    def update_metadata(code: str, ktype: str, update_info: dict = None, atype: str = None):
        mp = StorageManager.metadata_path(code, atype)
        meta = {}
        if mp.exists():
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, Exception):
                meta = {}
        meta["code"] = code
        meta["type"] = detect_asset_type(code)
        meta["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if update_info:
            meta.update(update_info)
        mp.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                      encoding="utf-8")

    @staticmethod
    def save_info(code: str, info: dict, atype: str = None):
        ip = StorageManager.info_path(code, atype)
        ip.parent.mkdir(parents=True, exist_ok=True)
        ip.write_text(json.dumps(info, ensure_ascii=False, indent=2),
                      encoding="utf-8")

    @staticmethod
    def get_cache_status(code: str) -> dict:
        status = {"code": code, "cached": False, "types": {}, "name": ""}
        d = get_data_dir(code)
        if not d.exists():
            return status
        status["cached"] = True
        for ktype in ("daily", "min15", "min1"):
            p = d / f"{ktype}.csv"
            if p.exists():
                df = pd.read_csv(p)
                date_col = get_ktype_col_date(ktype)
                if date_col in df.columns and not df.empty:
                    status["types"][ktype] = {
                        "count": len(df),
                        "start": str(df[date_col].iloc[-1])[:19],
                        "end":   str(df[date_col].iloc[0])[:19],
                    }
        ip = d / "info.json"
        if ip.exists():
            try:
                info = json.loads(ip.read_text(encoding="utf-8"))
                status["name"] = info.get("name", "")
            except Exception:
                pass
        if not status["name"]:
            status["name"] = TencentSource.get_name(code)
        return status

    @staticmethod
    def list_cached() -> list[dict]:
        dir_to_type = {v: k for k, v in _TYPE_DIR_MAP.items()}
        results = []
        for sub_dir in dir_to_type:
            p = DATA_ROOT / sub_dir
            if not p.exists():
                continue
            for d in sorted(p.iterdir()):
                if d.is_dir():
                    status = StorageManager.get_cache_status(d.name)
                    status["type"] = dir_to_type[sub_dir]
                    results.append(status)
        return results


# ── 数据获取调度 ──

def fetch_kline_incremental(code: str, ktype: str, force_type: str = None) -> tuple:
    date_col = get_ktype_col_date(ktype)
    atype = force_type or detect_asset_type(code)

    local_df = StorageManager.load_csv(code, ktype, atype)
    has_local = not local_df.empty

    if ktype == "daily":
        if atype == "index" or code.startswith("399"):
            new_df = SinaSource.fetch_daily_kline(code, atype=atype)
            if new_df.empty:
                new_df = TencentSource.fetch_daily_kline(code, atype=atype)
            if new_df.empty:
                new_df = EastMoneySource.fetch_kline(code, "daily")
        else:
            new_df = MootdxSource.fetch_kline(code, ktype)
            if new_df.empty:
                new_df = SinaSource.fetch_daily_kline(code, atype=atype)
            if new_df.empty:
                new_df = EastMoneySource.fetch_kline(code, "daily")
    else:
        new_df = TencentSource.fetch_mkline(code, ktype, atype=atype)
        if new_df.empty:
            new_df = EastMoneySource.fetch_kline(code, ktype)

    if new_df.empty:
        if has_local:
            return local_df, "无新数据"
        return pd.DataFrame(), "无法获取数据（非交易时间或代码无效）"

    if has_local:
        merged = pd.concat([local_df, new_df], ignore_index=True)
        merged = merged.drop_duplicates(subset=[date_col], keep="last")
        merged = merged.sort_values(by=date_col, ascending=False).reset_index(drop=True)

        if ktype == "daily":
            merged = merged[merged[date_col] >= DATE_RANGE["daily"]["start"]]
        elif ktype == "min15":
            cutoff = (datetime.now() -
                      timedelta(days=DATE_RANGE["min15"]["months"] * 30))
            merged = merged[merged[date_col] >= cutoff.strftime("%Y-%m-%d")]
        elif ktype == "min1":
            cutoff = (datetime.now() -
                      timedelta(days=DATE_RANGE["min1"]["days"]))
            merged = merged[merged[date_col] >= cutoff.strftime("%Y-%m-%d")]

        added = len(merged) - len(local_df)
        if added > 0:
            StorageManager.save_csv(code, ktype, merged, atype)
            StorageManager.update_metadata(code, ktype, atype=atype)
            return merged, f"新增 {added} 条（最新 {new_df[date_col].iloc[0][:19]}）"
        else:
            return local_df, "已是最新数据"
    else:
        if not new_df.empty:
            StorageManager.save_csv(code, ktype, new_df, atype)
            StorageManager.update_metadata(code, ktype, atype=atype)
            return new_df, f"首次获取 {len(new_df)} 条数据"
        return pd.DataFrame(), "无法获取数据"


def fetch_all_data(code: str, fetch_daily: bool = True,
                   fetch_min15: bool = True, fetch_min1: bool = True,
                   progress=None, force_type: str = None) -> str:
    code = normalize_code(code)
    if not code or len(code) != 6 or not code.isdigit():
        return "无效代码，请输入6位数字代码"

    atype = force_type or detect_asset_type(code)
    logs = []
    StorageManager.ensure_dirs(code, atype)

    name = TencentSource.get_name(code, atype)
    if name:
        StorageManager.save_info(code, {"code": code, "name": name}, atype)
        for kt in ("daily", "min15", "min1"):
            p = StorageManager.csv_path(code, kt, atype)
            if p.exists():
                try:
                    df = pd.read_csv(p)
                    if 'name' in df.columns and not df.empty and df['name'].iloc[0] != name:
                        df['name'] = name
                        df.to_csv(p, index=False, encoding="utf-8-sig")
                except Exception:
                    pass

    if fetch_daily:
        if progress:
            progress(0.1, "正在获取日K线数据...")
        df, msg = fetch_kline_incremental(code, "daily", force_type)
        logs.append(f"  | 日K线: {msg}")
        if not df.empty:
            dc = get_ktype_col_date('daily')
            logs.append(f"    | 范围: {df[dc].iloc[-1]} ~ {df[dc].iloc[0]}")
            logs.append(f"    | 行数: {len(df)}")

    if fetch_min15:
        if progress:
            progress(0.4, "正在获取15分钟K线数据...")
        df, msg = fetch_kline_incremental(code, "min15", force_type)
        logs.append(f"  | 15分钟K: {msg}")
        if not df.empty:
            dc = get_ktype_col_date('min15')
            logs.append(f"    | 范围: {df[dc].iloc[-1]} ~ {df[dc].iloc[0]}")
            logs.append(f"    | 行数: {len(df)}")

    if fetch_min1:
        if progress:
            progress(0.7, "正在获取1分钟K线数据...")
        df, msg = fetch_kline_incremental(code, "min1", force_type)
        logs.append(f"  | 1分钟K: {msg}")
        if not df.empty:
            dc = get_ktype_col_date('min1')
            logs.append(f"    | 范围: {df[dc].iloc[-1]} ~ {df[dc].iloc[0]}")
            logs.append(f"    | 行数: {len(df)}")

    if progress:
        progress(1.0, "完成")

    logs.insert(0, f"  {code} {name} 数据处理完成")
    return "\n".join(logs)


def batch_update_all(progress=None) -> str:
    cached_list = StorageManager.list_cached()
    if not cached_list:
        return "暂无缓存数据"

    total = len(cached_list)
    logs = [f"开始批量更新 {total} 只股票..."]
    success = 0
    fail = 0

    for i, status in enumerate(cached_list):
        code = status["code"]
        name = status.get("name", "")
        if progress:
            progress((i + 1) / total, f"({i+1}/{total}) {code} {name}")

        try:
            result = fetch_all_data(code, fetch_daily=True,
                                    fetch_min15=True, fetch_min1=True)
            if "新增" in result or "首次" in result or "已是最新" in result:
                success += 1
                logs.append(f"  {code} {name}: 成功")
            else:
                fail += 1
                logs.append(f"  {code} {name}: {result[:60]}")
        except Exception as e:
            fail += 1
            logs.append(f"  {code} {name}: {e}")

        if i < total - 1:
            time.sleep(0.5)

    logs.append(f"\n批量更新完成: 成功 {success}, 失败 {fail}")
    return "\n".join(logs)
