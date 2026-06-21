#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K线图绘制模块 — 使用 matplotlib 绘制 K 线 + MA 均线。
输出为图片路径，可在 Gradio 中展示。
"""

import os
import tempfile
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # 非交互后端
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
from tools.indicators import ensure_ascending, calc_ma


# ── 中文字体回退 ──────────────────────────────────────────────
try:
    plt.rcParams["font.sans-serif"] = ["WenQuanYi Micro Hei", "Noto Sans CJK SC",
                                        "SimHei", "Microsoft YaHei", "Arial Unicode MS",
                                        "DejaVu Sans"]
except Exception:
    pass
plt.rcParams["axes.unicode_minus"] = False


def plot_kline(df: pd.DataFrame, code: str = "", name: str = "",
               ktype: str = "daily", ma_periods: list = None,
               max_bars: int = 120) -> str:
    """
    绘制 K 线图 + MA 均线，返回图片文件路径。

    参数
    ----
    df : DataFrame
        必须包含: open, high, low, close, 以及日期列(date或datetime)。
    code : str
        股票代码。
    name : str
        股票名称。
    ktype : str
        K线类型 (daily/min15/min1)。
    ma_periods : list
        要显示的均线周期，默认 [5, 10, 20]。
    max_bars : int
        最多绘制的K线根数（取最新的N根）。

    返回
    ----
    str — 图片文件路径，失败返回空字符串。
    """
    if df is None or df.empty:
        return ""

    if ma_periods is None:
        ma_periods = [5, 10, 20]

    date_col = "datetime" if ktype != "daily" else "date"
    if date_col not in df.columns:
        date_col = "date" if "date" in df.columns else "datetime"
        if date_col not in df.columns:
            return ""

    plot_df = ensure_ascending(df.copy(), date_col)
    if len(plot_df) > max_bars:
        plot_df = plot_df.tail(max_bars).reset_index(drop=True)

    # 计算 MA
    plot_df = calc_ma(plot_df, periods=ma_periods)

    dates = pd.to_datetime(plot_df[date_col])
    opens = plot_df["open"].astype(float).values
    highs = plot_df["high"].astype(float).values
    lows = plot_df["low"].astype(float).values
    closes = plot_df["close"].astype(float).values

    # ── 创建图表 ──
    fig, ax = plt.subplots(figsize=(12, 6), facecolor="#f8f9fa")
    ax.set_facecolor("#f8f9fa")

    n = len(dates)
    width = 0.6
    gap = 1
    step = width + gap

    # 获取最高价和最低价（Q13 优化）
    overall_high = highs.max()
    overall_low = lows.min()
    overall_high_idx = highs.argmax()
    overall_low_idx = lows.argmin()

    # 绘制 K 线
    up_color = "#ef5350"
    down_color = "#26a69a"

    for i in range(n):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        color = up_color if c >= o else down_color
        x = i * step

        # 影线
        ax.plot([x, x], [l, h], color=color, linewidth=1, zorder=2)
        # 实体
        rect = plt.Rectangle((x - width / 2, min(o, c)), width, abs(c - o),
                             facecolor=color, edgecolor=color, linewidth=0.5,
                             zorder=3)
        ax.add_patch(rect)

    # ── 绘制 MA 线 ──
    colors = {5: "#f39c12", 10: "#2196f3", 20: "#9c27b0"}
    for p in ma_periods:
        col = f"MA{p}"
        if col in plot_df.columns:
            ma_vals = plot_df[col].values
            x_vals = [i * step for i in range(len(ma_vals))]
            ax.plot(x_vals, ma_vals, color=colors.get(p, "#666"),
                    linewidth=1.2, label=f"MA{p}", zorder=4)

    # ── 标注最高价和最低价（Q13） ──
    ax.annotate(f"最高 {overall_high:.2f}",
                xy=(overall_high_idx * step, overall_high),
                xytext=(0, -18), textcoords="offset points",
                fontsize=9, fontweight="bold", color="#ef5350",
                ha="center",
                arrowprops=dict(arrowstyle="->", color="#ef5350", lw=1),
                bbox=dict(boxstyle="round,pad=0.2", facecolor="#ffe0e0",
                          edgecolor="#ef5350", alpha=0.8))
    ax.annotate(f"最低 {overall_low:.2f}",
                xy=(overall_low_idx * step, overall_low),
                xytext=(0, 18), textcoords="offset points",
                fontsize=9, fontweight="bold", color="#26a69a",
                ha="center",
                arrowprops=dict(arrowstyle="->", color="#26a69a", lw=1),
                bbox=dict(boxstyle="round,pad=0.2", facecolor="#e0f2f1",
                          edgecolor="#26a69a", alpha=0.8))

    # ── X 轴标签（修正：使用实际 K 线 x 坐标 i*step，而非索引值） ──
    tick_step = max(1, n // 10)
    tick_indices = list(range(0, n, tick_step))
    tick_positions = [i * step for i in tick_indices]
    tick_labels = [dates[i].strftime("%m-%d" if ktype == "daily" else "%m-%d %H:%M")
                   for i in tick_indices]
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels, rotation=45, ha="right", fontsize=9)

    # ── Y 轴 ──
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.2f}"))

    # 添加10%价格余量
    price_margin = (overall_high - overall_low) * 0.1
    ax.set_ylim(overall_low - price_margin, overall_high + price_margin)

    # ── 标题与标签 ──
    type_label = {"daily": "日K线", "min15": "15分钟K线", "min1": "1分钟K线"}
    title = f"{code} {name} — {type_label.get(ktype, ktype)}"
    if n == max_bars:
        title += f" (最新{max_bars}根)"
    ax.set_title(title, fontsize=14, fontweight="bold", pad=12)
    ax.set_xlabel("")
    ax.set_ylabel("价格", fontsize=10)

    # ── 网格与图例 ──
    ax.grid(True, alpha=0.3, linestyle="--")
    ax.legend(loc="best", fontsize=9, framealpha=0.8)

    # ── 价格标签（最新收盘价，修正：使用正确 x 坐标） ──
    last_close = closes[-1]
    last_x = (n - 1) * step
    ax.annotate(f"{last_close:.2f}",
                xy=(last_x, last_close),
                xytext=(12, 0), textcoords="offset points",
                fontsize=11, fontweight="bold", color="#333",
                va="center",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="yellow",
                          edgecolor="#ccc", alpha=0.8))

    plt.tight_layout()

    # ── 保存到临时文件 ──
    try:
        tmp_dir = tempfile.gettempdir()
        img_path = os.path.join(tmp_dir,
                                f"kline_{code}_{ktype}_{datetime.now().strftime('%Y%m%d%H%M%S')}.png")
        fig.savefig(img_path, dpi=150, bbox_inches="tight",
                    facecolor=fig.get_facecolor())
        plt.close(fig)
        return img_path
    except Exception:
        plt.close(fig)
        return ""
