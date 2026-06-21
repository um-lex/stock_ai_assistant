#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
趋势判断模块 — 纯程序化判断，无 AI 幻觉。

策略来源
--------
1. 均线排列策略 (Moving Average Crossover)
   - MA5 > MA10 > MA20 → 趋势上涨
   - MA5 < MA10 < MA20 → 趋势下跌
   - 均线交叉缠绕 → 趋势震荡

2. 价格相对位置
   - 价格在 MA20 上方运行 → 偏强
   - 价格在 MA20 下方运行 → 偏弱
   - 价格围绕 MA20 上下波动 → 震荡

3. 综合判定
   - 结合均线排列 + 价格位置 + 趋势斜率
"""

import pandas as pd
import numpy as np
from datetime import datetime
from tools.indicators import calc_ma, ensure_ascending


def judge_trend(df: pd.DataFrame) -> dict:
    """
    综合判断股票趋势。

    参数
    ----
    df : DataFrame
        必须包含 close 列（日K线数据），按时间降序或升序均可。

    返回
    ----
    dict = {
        "trend": "up" | "down" | "range" | "unknown",
        "trend_label": "趋势上涨" | "趋势下跌" | "趋势震荡" | "数据不足",
        "confidence": 0.0~1.0,
        "detail": { ... }  # 各子策略输出
    }
    """
    if df is None or df.empty or len(df) < 20:
        return {
            "trend": "unknown",
            "trend_label": "数据不足",
            "confidence": 0.0,
            "detail": {"reason": f"需要至少20根日K线，当前{len(df) if df is not None else 0}条"}
        }

    # 确保升序
    df_sorted = ensure_ascending(df.copy())

    close = df_sorted["close"].astype(float).values
    ma5 = df_sorted["close"].rolling(5, min_periods=1).mean().values
    ma10 = df_sorted["close"].rolling(10, min_periods=1).mean().values
    ma20 = df_sorted["close"].rolling(20, min_periods=1).mean().values

    latest = close[-1]
    l_ma5 = ma5[-1]
    l_ma10 = ma10[-1]
    l_ma20 = ma20[-1]

    # --- 策略1: 均线排列 ---
    # 取最近 N 根判断排列稳定性
    n_check = min(10, len(df_sorted))
    ma5_good = sum(ma5[-i] > ma10[-i] > ma20[-i] for i in range(1, n_check + 1)) / n_check
    ma5_bad = sum(ma5[-i] < ma10[-i] < ma20[-i] for i in range(1, n_check + 1)) / n_check

    # --- 策略2: 价格相对位置 ---
    price_above_ma20 = latest > l_ma20
    price_near_ma20 = abs(latest - l_ma20) / l_ma20 < 0.03 if l_ma20 != 0 else False

    # --- 策略3: 趋势斜率（线性回归斜率） ---
    x = np.arange(min(20, len(df_sorted)))
    y = close[-min(20, len(df_sorted)):]
    if len(x) >= 5:
        slope = np.polyfit(x, y, 1)[0]
    else:
        slope = 0
    slope_pct = slope / (l_ma20 if l_ma20 != 0 else latest) * 100

    # --- 综合判定 ---
    up_score = 0
    down_score = 0

    # 均线排列得分
    if ma5_good > 0.6:
        up_score += 3
    elif ma5_good > 0.3:
        up_score += 1
    if ma5_bad > 0.6:
        down_score += 3
    elif ma5_bad > 0.3:
        down_score += 1

    # 价格位置得分
    if price_above_ma20:
        up_score += 2
    else:
        down_score += 2

    # 斜率得分
    if slope_pct > 0.5:
        up_score += 2
    elif slope_pct > 0.1:
        up_score += 1
    elif slope_pct < -0.5:
        down_score += 2
    elif slope_pct < -0.1:
        down_score += 1

    # MACD 快慢线位置（简化: 用 MA5/MA20 差代替）
    ma_diff = ma5[-1] - ma20[-1]
    ma_diff_prev = ma5[-2] - ma20[-2] if len(ma5) >= 2 else 0
    if ma_diff > 0 and ma_diff > ma_diff_prev:
        up_score += 1
    elif ma_diff < 0 and ma_diff < ma_diff_prev:
        down_score += 1

    # --- 最终判定 ---
    total = up_score + down_score
    if total == 0:
        trend = "range"
    else:
        up_ratio = up_score / total
        if up_ratio >= 0.65:
            trend = "up"
        elif up_ratio <= 0.35:
            trend = "down"
        else:
            trend = "range"

    confidence = max(up_score, down_score) / (total + 1) * 0.8 + 0.2

    return {
        "trend": trend,
        "trend_label": {"up": "趋势上涨", "down": "趋势下跌", "range": "趋势震荡"}.get(trend, "未知"),
        "confidence": round(confidence, 2),
        "detail": {
            "ma5_good_ratio": round(ma5_good, 2),
            "ma5_bad_ratio": round(ma5_bad, 2),
            "price_above_ma20": bool(price_above_ma20),
            "price_near_ma20": bool(price_near_ma20),
            "slope_pct": round(slope_pct, 2),
            "ma5_ma20_diff": round(float(ma_diff), 4),
            "up_score": up_score,
            "down_score": down_score,
            "ma5": round(float(l_ma5), 2),
            "ma10": round(float(l_ma10), 2),
            "ma20": round(float(l_ma20), 2),
            "latest_close": round(float(latest), 2),
        }
    }


def find_trend_turning_point(df: pd.DataFrame) -> dict:
    """
    查找最近一次趋势拐点（趋势类型转变的时间点）。

    参数
    ----
    df : DataFrame
        日K线数据，需含 close 和 date/datetime 列。

    返回
    ----
    dict = {
        "had_turning": bool,      # 是否存在拐点
        "current_trend": str,     # 当前趋势 label
        "prev_trend": str,        # 之前趋势 label
        "change_date": str,       # 拐点日期
        "days_since_change": int, # 拐点距今多少日
        "trend_history": list     # 近 N 日的趋势序列（用于调试）
    }
    """
    if df is None or df.empty or len(df) < 25:
        return {"had_turning": False, "current_trend": "数据不足",
                "prev_trend": "", "change_date": "", "days_since_change": -1,
                "trend_history": []}

    df_sorted = ensure_ascending(df.copy())
    date_col = "datetime" if "datetime" in df_sorted.columns else "date"

    # 从后往前扫描，对每个窗口判断趋势（窗口大小=20，步长=1）
    min_window = 20
    trends = []
    max_lookback = min(60, len(df_sorted) - min_window + 1)

    for i in range(len(df_sorted) - min_window, len(df_sorted) - min_window - max_lookback, -1):
        if i < 0:
            break
        window = df_sorted.iloc[:i + min_window]
        result = judge_trend(window)
        trends.append({
            "date": str(window[date_col].iloc[-1])[:10],
            "trend": result["trend"],
        })

    trends.reverse()  # 时间升序

    if len(trends) < 2:
        return {"had_turning": False, "current_trend": "数据不足",
                "prev_trend": "", "change_date": "", "days_since_change": -1,
                "trend_history": trends}

    current = trends[-1]["trend"]
    change_date = ""
    prev_trend = ""
    change_idx = -1

    # 从后往前找第一个趋势变化
    for i in range(len(trends) - 1, 0, -1):
        if trends[i]["trend"] != trends[i - 1]["trend"]:
            change_date = trends[i]["date"]
            prev_trend = trends[i - 1]["trend"]
            change_idx = i
            break

    if change_idx < 0:
        # 整个区间无变化
        return {"had_turning": False, "current_trend": {"up": "趋势上涨", "down": "趋势下跌",
                "range": "趋势震荡"}.get(current, "未知"),
                "prev_trend": "持续", "change_date": "", "days_since_change": -1,
                "trend_history": trends}

    days_since = len(trends) - 1 - change_idx
    last_trend_label = {"up": "趋势上涨", "down": "趋势下跌", "range": "趋势震荡"}
    prev_trend_label = {"up": "趋势上涨", "down": "趋势下跌", "range": "趋势震荡"}

    return {
        "had_turning": True,
        "current_trend": last_trend_label.get(current, "未知"),
        "prev_trend": prev_trend_label.get(prev_trend, "未知") + " → " + last_trend_label.get(current, "未知"),
        "change_date": change_date,
        "days_since_change": days_since,
        "trend_history": trends[-10:],  # 最近10个窗口的趋势
    }


def generate_strategy_report() -> str:
    """
    返回当前使用的趋势判断策略说明文本，供用户保存参考。
    """
    return """========== 趋势判断策略说明 ==========

【策略1: 均线排列 (MA Crossover)】
  - MA5 > MA10 > MA20 → 短期/中期/长期均线多头排列 → 上涨信号
  - MA5 < MA10 < MA20 → 空头排列 → 下跌信号
  - 统计近10根K线中多头排列的占比

【策略2: 价格相对位置】
  - 收盘价在 MA20 上方 → 偏强，加分
  - 收盘价在 MA20 下方 → 偏弱，减分
  - 收盘价在 MA20 上下3%以内 → 视为震荡

【策略3: 趋势斜率】
  - 对最近20根K线的收盘价做线性回归
  - 斜率 > 0.5%/bar → 上升趋势显著
  - 斜率 < -0.5%/bar → 下降趋势显著

【策略4: MACD 简化】
  - 用 MA5 - MA20 差值模拟 MACD 快慢线
  - 差值扩大且为正 → 上涨动能增强
  - 差值扩大且为负 → 下跌动能增强

【综合判定】
  - 四项策略分别打分（上涨得分 / 下跌得分）
  - 上涨占比 ≥ 65% → 趋势上涨
  - 下跌占比 ≥ 65% → 趋势下跌
  - 其余 → 趋势震荡
============================================="""
