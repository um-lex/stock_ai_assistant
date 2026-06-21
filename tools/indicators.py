#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
技术指标计算模块（纯程序计算，无AI幻觉）
提供 MA / MACD / RSI / 等常用指标的向量化计算。
"""

import pandas as pd
import numpy as np


def calc_ma(df: pd.DataFrame, periods: list = None, col: str = "close") -> pd.DataFrame:
    """
    计算移动均线，返回增加了 MA 列的 DataFrame（副本操作）。

    参数
    ----
    df : DataFrame
        必须包含 col 指定的价格列，已按时间升序排列。
    periods : list[int]
        均线周期列表，默认 [5, 10, 20]。
    col : str
        用于计算的价格列名。

    返回
    ----
    DataFrame — 新增 MA5, MA10, MA20 等列（最新行可能为 NaN）。
    """
    if df is None or df.empty:
        return df
    if periods is None:
        periods = [5, 10, 20]
    result = df.copy()
    for p in periods:
        result[f"MA{p}"] = result[col].rolling(window=p, min_periods=1).mean()
    return result


def ensure_ascending(df: pd.DataFrame, date_col: str = None) -> pd.DataFrame:
    """确保 DataFrame 按时间升序排列（指标计算需要升序）。"""
    if df.empty:
        return df
    if date_col is None:
        # 自动检测日期列
        for c in ["datetime", "date"]:
            if c in df.columns:
                date_col = c
                break
    if date_col and date_col in df.columns:
        first_val = df[date_col].iloc[0]
        last_val = df[date_col].iloc[-1]
        # 如果第一个值比最后一个大 -> 降序，需要反转
        if isinstance(first_val, str):
            if first_val > last_val:
                df = df.sort_values(by=date_col, ascending=True).reset_index(drop=True)
        else:
            if first_val > last_val:
                df = df.sort_values(by=date_col, ascending=True).reset_index(drop=True)
    return df


def add_ma_to_csv(csv_path: str, periods: list = None) -> pd.DataFrame:
    """
    读取 CSV -> 计算 MA -> 写回（追加 MA 列），返回含 MA 的 DataFrame。
    """
    if not csv_path or not csv_path.exists():
        return pd.DataFrame()
    df = pd.read_csv(csv_path)
    if df.empty:
        return df
    df = ensure_ascending(df)
    df = calc_ma(df, periods=periods)
    # 写回时保持降序（最新在前）
    date_col = "datetime" if "datetime" in df.columns else "date"
    if date_col in df.columns:
        df = df.sort_values(by=date_col, ascending=False).reset_index(drop=True)
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    return df


def get_ma_values(df: pd.DataFrame, period: int, col: str = "close") -> pd.Series:
    """获取指定周期的 MA 值序列。"""
    if df.empty:
        return pd.Series(dtype=float)
    return df[col].rolling(window=period, min_periods=1).mean()
