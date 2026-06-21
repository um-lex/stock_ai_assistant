"""数据源：通达信/ 腾讯 / 新浪 / 东方财富"""

import json
import time
import random
import re
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from config import (
    get_market, get_mootdx_market, get_ktype_category, get_ktype_col_date,
    DATE_RANGE, OFFSET, UA, em_get, fmt_time, INDEX_CODES, Quotes,
)


class MootdxSource:
    """mootdx TCP 行情数据源"""

    _client = None

    @classmethod
    def get_client(cls):
        if cls._client is None:
            if Quotes is None:
                raise RuntimeError("mootdx 未安装: pip install mootdx")
            cls._client = Quotes.factory(market='std')
        return cls._client

    @classmethod
    def fetch_kline(cls, code: str, ktype: str) -> pd.DataFrame:
        if ktype != "daily":
            raise ValueError(f"mootdx 仅支持 daily，{ktype} 请用 TencentSource.fetch_mkline")
        if ktype not in OFFSET:
            raise ValueError(f"不支持的K线类型: {ktype}")

        client = cls.get_client()
        market = get_mootdx_market(code)
        category = get_ktype_category(ktype)
        offset = OFFSET[ktype]

        try:
            raw = client.bars(symbol=code, category=category,
                              offset=offset, market=market)
        except Exception as e:
            raise RuntimeError(f"mootdx 请求失败 [{code} {ktype}]: {e}")

        if raw is None or (hasattr(raw, 'empty') and raw.empty):
            return pd.DataFrame()

        if not isinstance(raw, pd.DataFrame):
            raw = pd.DataFrame(raw)

        col_map = {}
        for c in raw.columns:
            cl = str(c).lower().strip()
            if cl in ('date', 'datetime', '时间', '日期'):
                col_map[c] = 'dt_col'
            elif cl in ('open', '开盘', '开盘价'):
                col_map[c] = 'open'
            elif cl in ('close', '收盘', '收盘价'):
                col_map[c] = 'close'
            elif cl in ('high', '最高', '最高价'):
                col_map[c] = 'high'
            elif cl in ('low', '最低', '最低价'):
                col_map[c] = 'low'
            elif cl in ('vol', 'volume', '成交量', 'volume_ratio'):
                col_map[c] = 'volume'
            elif cl in ('amount', '成交额', '成交金额', 'turnover', 'turnoverrate'):
                col_map[c] = 'amount'
            elif cl in ('code', '代码'):
                col_map[c] = 'code'

        has_date = any(v == 'dt_col' for v in col_map.values())
        if not has_date:
            first_col = raw.columns[0]
            col_map[first_col] = 'dt_col'

        df = raw.rename(columns=col_map)
        df = df.loc[:, ~df.columns.duplicated()]
        df = df.reset_index(drop=True)
        date_col_name = get_ktype_col_date(ktype)
        if 'dt_col' in df.columns:
            df[date_col_name] = df['dt_col'].apply(fmt_time)
            df = df.drop(columns=['dt_col'])

        needed = {'open', 'close', 'high', 'low', 'volume', 'amount', date_col_name}
        missing = needed - set(df.columns)
        if missing:
            raise RuntimeError(f"mootdx 返回缺少列 {missing}，实际列: {list(df.columns)}")

        if ktype == "daily":
            cutoff = DATE_RANGE["daily"]["start"]
            df = df[df[date_col_name] >= cutoff]
        elif ktype == "min15":
            cutoff = (datetime.now() - timedelta(days=DATE_RANGE["min15"]["months"] * 30)
                      ).strftime("%Y-%m-%d")
            df = df[df[date_col_name] >= cutoff]
        elif ktype == "min1":
            cutoff = (datetime.now() - timedelta(days=DATE_RANGE["min1"]["days"])
                      ).strftime("%Y-%m-%d")
            df = df[df[date_col_name] >= cutoff]

        df['code'] = code
        df['name'] = ''
        df = df.sort_values(by=date_col_name, ascending=False).reset_index(drop=True)
        cols = [date_col_name, 'code', 'name', 'open', 'high', 'low', 'close', 'volume', 'amount']
        cols = [c for c in cols if c in df.columns]
        return df[cols]


class TencentSource:
    """腾讯财经实时行情"""

    @staticmethod
    def get_realtime(code: str, atype: str = None) -> dict:
        market = get_market(code, atype)
        url = f"https://qt.gtimg.cn/q={market}{code}"
        req = urllib.request.Request(url)
        req.add_header("User-Agent", UA)
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            data = resp.read().decode("gbk")
        except Exception as e:
            return {"error": str(e)}

        if '"' not in data:
            return {"error": "未找到该代码数据"}

        try:
            vals = data.split('"')[1].split("~")
        except (IndexError, ValueError):
            return {"error": "数据解析失败"}

        if len(vals) < 53:
            return {"error": f"字段不足({len(vals)})"}

        def sf(v, default=0):
            try:
                return float(v) if v else default
            except ValueError:
                return default

        return {
            "name": vals[1],
            "price": sf(vals[3]),
            "last_close": sf(vals[4]),
            "open": sf(vals[5]),
            "high": sf(vals[33]),
            "low": sf(vals[34]),
            "change_amt": sf(vals[31]),
            "change_pct": sf(vals[32]),
            "amount_wan": sf(vals[37]),
            "turnover_pct": sf(vals[38]),
            "pe_ttm": sf(vals[39]),
            "amplitude_pct": sf(vals[43]),
            "mcap_yi": sf(vals[44]),
            "float_mcap_yi": sf(vals[45]),
            "pb": sf(vals[46]),
            "limit_up": sf(vals[47]),
            "limit_down": sf(vals[48]),
        }

    @staticmethod
    def get_name(code: str, atype: str = None) -> str:
        return TencentSource.get_realtime(code, atype).get("name", "")

    @staticmethod
    def fetch_daily_kline(code: str, atype: str = None) -> pd.DataFrame:
        market = get_market(code, atype)
        url = f"http://data.gtimg/flashdata/hushen/latest/daily/{market}{code}.js"
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=15)
            text = resp.text
        except Exception:
            return pd.DataFrame()

        rows = []
        for line in text.split("\\n"):
            line = line.strip().strip('"').strip("'")
            if line.startswith("date:"):
                try:
                    date_str = line.split(":")[1].strip().replace("/", "-")
                except Exception:
                    continue
                current_date = date_str
            elif line.startswith("OHLC:"):
                try:
                    parts = line.split(":")[1].strip().split(",")
                    if len(parts) >= 4:
                        rows.append({
                            "date": current_date[:10],
                            "open": float(parts[0]),
                            "close": float(parts[1]),
                            "high": float(parts[2]),
                            "low": float(parts[3]),
                            "volume": 0,
                            "amount": 0,
                        })
                except (ValueError, IndexError):
                    continue

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df['code'] = code
        df['name'] = ''
        cutoff = DATE_RANGE["daily"]["start"]
        df = df[df["date"] >= cutoff]
        df = df.drop_duplicates(subset=["date"], keep="last")
        df = df.sort_values(by="date", ascending=False).reset_index(drop=True)
        cols = ["date", "code", "name", "open", "high", "low", "close", "volume", "amount"]
        return df[cols]

    @staticmethod
    def fetch_mkline(code: str, ktype: str, atype: str = None) -> pd.DataFrame:
        if ktype not in ("min15", "min1"):
            raise ValueError(f"不支持的K线类型: {ktype}")

        market = get_market(code, atype)
        param = f"{market}{code},m{'1' if ktype=='min1' else '15'},,800"
        url = f"http://ifzq.gtimg.cn/appstock/app/kline/mkline?param={param}"

        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=15)
            data = resp.json()
        except Exception as e:
            raise RuntimeError(f"腾讯分钟K线请求失败 [{code} {ktype}]: {e}")

        key = f"{market}{code}"
        kline_key = "m1" if ktype == "min1" else "m15"
        bars = data.get("data", {}).get(key, {}).get(kline_key, [])
        if not bars:
            return pd.DataFrame()

        rows = []
        for bar in bars:
            if len(bar) < 6:
                continue
            dt_str = str(bar[0])
            if len(dt_str) != 12:
                continue
            try:
                dt = f"{dt_str[:4]}-{dt_str[4:6]}-{dt_str[6:8]} {dt_str[8:10]}:{dt_str[10:12]}"
            except Exception:
                continue
            try:
                row = {
                    "datetime": dt,
                    "open": float(bar[1]),
                    "close": float(bar[2]),
                    "high": float(bar[3]),
                    "low": float(bar[4]),
                    "volume": float(bar[5]) if bar[5] else 0,
                    "amount": float(bar[7]) if len(bar) > 7 and bar[7] else 0,
                }
            except (ValueError, IndexError):
                continue
            rows.append(row)

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df['code'] = code
        df['name'] = ''

        if ktype == "min15":
            cutoff = (datetime.now() - timedelta(days=DATE_RANGE["min15"]["months"] * 30)
                      ).strftime("%Y-%m-%d")
            df = df[df["datetime"] >= cutoff]
        elif ktype == "min1":
            cutoff = (datetime.now() - timedelta(days=DATE_RANGE["min1"]["days"])
                      ).strftime("%Y-%m-%d")
            df = df[df["datetime"] >= cutoff]

        df = df.drop_duplicates(subset=["datetime"], keep="last")
        df = df.sort_values(by="datetime", ascending=False).reset_index(drop=True)
        cols = ["datetime", "code", "name", "open", "high", "low", "close", "volume", "amount"]
        return df[cols]


class SinaSource:
    """新浪财经数据源（HTTP）"""

    @staticmethod
    def fetch_daily_kline(code: str, datalen: int = 800, atype: str = None) -> pd.DataFrame:
        market = get_market(code, atype)
        symbol = f"{market}{code}"
        url = (f"http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php"
               f"/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={datalen}")
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=15)
            data = resp.json()
        except Exception:
            return pd.DataFrame()

        if not data or not isinstance(data, list):
            return pd.DataFrame()

        rows = []
        for bar in data:
            try:
                row = {
                    "date": str(bar.get("day", bar.get("date", "")))[:10],
                    "open": float(bar.get("open", 0)),
                    "close": float(bar.get("close", 0)),
                    "high": float(bar.get("high", 0)),
                    "low": float(bar.get("low", 0)),
                    "volume": float(bar.get("volume", 0)) if bar.get("volume") else 0,
                    "amount": float(bar.get("amount", 0)) if bar.get("amount") else 0,
                }
            except (ValueError, TypeError):
                continue
            rows.append(row)

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df['code'] = code
        df['name'] = ''
        cutoff = DATE_RANGE["daily"]["start"]
        df = df[df["date"] >= cutoff]
        df = df.drop_duplicates(subset=["date"], keep="last")
        df = df.sort_values(by="date", ascending=False).reset_index(drop=True)
        cols = ["date", "code", "name", "open", "high", "low", "close", "volume", "amount"]
        return df[cols]


class EastMoneySource:
    """东财数据源"""

    @staticmethod
    def datacenter(report_name: str, columns: str = "ALL",
                   filter_str: str = "", page_size: int = 50,
                   sort_columns: str = "", sort_types: str = "-1") -> list:
        params = {
            "reportName": report_name, "columns": columns,
            "filter": filter_str, "pageNumber": "1", "pageSize": str(page_size),
            "sortColumns": sort_columns, "sortTypes": sort_types,
            "source": "WEB", "client": "WEB",
        }
        r = em_get("https://datacenter-web.eastmoney.com/api/data/v1/get",
                    params=params, timeout=15)
        d = r.json()
        if d.get("result") and d["result"].get("data"):
            return d["result"]["data"]
        return []

    @staticmethod
    def stock_info(code: str) -> dict:
        market_code = 1 if code.startswith("6") else 0
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        params = {
            "fltt": "2", "invt": "2",
            "fields": "f57,f58,f84,f85,f127,f116,f117,f189,f43",
            "secid": f"{market_code}.{code}",
        }
        r = em_get(url, params=params, timeout=10)
        d = r.json().get("data", {})
        if not d:
            return {}
        return {
            "code": d.get("f57", ""),
            "name": d.get("f58", ""),
            "industry": d.get("f127", ""),
            "total_shares": d.get("f84", 0),
            "float_shares": d.get("f85", 0),
            "mcap": d.get("f116", 0),
            "float_mcap": d.get("f117", 0),
            "list_date": str(d.get("f189", "")),
            "price": d.get("f43", 0),
        }

    @staticmethod
    def fund_flow_minute(code: str) -> list:
        secid = f"1.{code}" if code.startswith("6") else f"0.{code}"
        url = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
        params = {
            "secid": secid, "klt": 1,
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57",
        }
        headers = {"Referer": "https://quote.eastmoney.com/"}
        try:
            r = em_get(url, params=params, headers=headers, timeout=10)
            d = r.json()
        except Exception:
            return []
        rows = []
        for line in d.get("data", {}).get("klines", []):
            parts = line.split(",")
            if len(parts) >= 6:
                rows.append({
                    "time": parts[0],
                    "main_net": float(parts[1]),
                    "small_net": float(parts[2]),
                    "mid_net": float(parts[3]),
                    "large_net": float(parts[4]),
                    "super_net": float(parts[5]),
                })
        return rows

    @staticmethod
    def fund_flow_120d(code: str) -> list:
        market_code = 1 if code.startswith("6") else 0
        url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
        params = {
            "secid": f"{market_code}.{code}",
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
            "lmt": "120",
        }
        headers = {"Referer": "https://quote.eastmoney.com/"}
        try:
            r = em_get(url, params=params, headers=headers, timeout=15)
            d = r.json()
        except Exception:
            return []
        rows = []
        for line in d.get("data", {}).get("klines", []):
            parts = line.split(",")
            if len(parts) >= 7:
                rows.append({
                    "date": parts[0],
                    "main_net": float(parts[1]) if parts[1] != "-" else 0,
                    "small_net": float(parts[2]) if parts[2] != "-" else 0,
                    "mid_net": float(parts[3]) if parts[3] != "-" else 0,
                    "large_net": float(parts[4]) if parts[4] != "-" else 0,
                    "super_net": float(parts[5]) if parts[5] != "-" else 0,
                })
        return rows

    @staticmethod
    def concept_blocks(code: str) -> dict:
        market_code = 1 if code.startswith("6") else 0
        params = {
            "fltt": "2", "invt": "2",
            "secid": f"{market_code}.{code}",
            "spt": "3", "pi": "0", "pz": "200", "po": "1",
            "fields": "f12,f14,f3,f128",
        }
        headers = {"Referer": "https://quote.eastmoney.com/"}
        try:
            r = em_get("https://push2.eastmoney.com/api/qt/slist/get",
                       params=params, headers=headers, timeout=15)
            d = r.json()
        except Exception:
            return {"total": 0, "boards": [], "concept_tags": []}
        diff = (d.get("data") or {}).get("diff") or {}
        items = diff.values() if isinstance(diff, dict) else diff
        boards = []
        for it in items:
            boards.append({
                "name": it.get("f14", ""),
                "code": it.get("f12", ""),
                "change_pct": it.get("f3", ""),
                "lead_stock": it.get("f128", ""),
            })
        return {"total": len(boards), "boards": boards,
                "concept_tags": [b["name"] for b in boards]}

    @staticmethod
    def margin_trading(code: str, page_size: int = 30) -> list:
        data = EastMoneySource.datacenter(
            "RPTA_WEB_RZRQ_GGMX",
            filter_str=f'(SCODE="{code}")',
            page_size=page_size,
            sort_columns="DATE", sort_types="-1",
        )
        rows = []
        for row in data:
            rows.append({
                "date": str(row.get("DATE", ""))[:10],
                "rzye": row.get("RZYE", 0),
                "rzmre": row.get("RZMRE", 0),
                "rqye": row.get("RQYE", 0),
                "rzrqye": row.get("RZRQYE", 0),
            })
        return rows

    @staticmethod
    def fetch_kline(code: str, ktype: str = "daily", limit: int = 800) -> pd.DataFrame:
        klt_map = {"daily": 101, "min15": 15, "min1": 1}
        klt = klt_map.get(ktype, 101)
        market_code = 1 if code.startswith(("6", "9", "000", "001", "002", "003")) else 0

        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        params = {
            "secid": f"{market_code}.{code}",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": str(klt),
            "fqt": "1",
            "lmt": str(limit),
        }
        headers = {"Referer": "https://quote.eastmoney.com/"}

        try:
            r = em_get(url, params=params, headers=headers, timeout=15)
            d = r.json()
        except Exception:
            return pd.DataFrame()

        klines = d.get("data", {}).get("klines", [])
        if not klines:
            return pd.DataFrame()

        date_col = "datetime" if ktype != "daily" else "date"
        rows = []
        for line in klines:
            parts = line.split(",")
            if len(parts) < 11:
                continue
            try:
                dt_val = str(parts[0]).strip()
                if ktype == "daily":
                    dt_val = dt_val[:10]
                row = {
                    date_col: dt_val,
                    "open": float(parts[1]),
                    "close": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "volume": float(parts[5]),
                    "amount": float(parts[6]),
                }
            except (ValueError, IndexError):
                continue
            rows.append(row)

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df['code'] = code
        df['name'] = ''

        if ktype == "daily":
            df = df[df[date_col] >= DATE_RANGE["daily"]["start"]]

        df = df.drop_duplicates(subset=[date_col], keep="last")
        df = df.sort_values(by=date_col, ascending=False).reset_index(drop=True)
        cols = [date_col, "code", "name", "open", "high", "low", "close", "volume", "amount"]
        return df[cols]
