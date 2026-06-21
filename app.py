"""Gradio 多标签页 UI"""

import csv
import io
import json
import shutil
from datetime import datetime
from pathlib import Path

from config import (
    gr, DATA_ROOT, DATE_RANGE, MA_PERIODS,
    normalize_code, detect_asset_type, get_data_dir, get_ktype_col_date,
    load_config, save_config,
)
from storage import StorageManager, search_stock_by_name, fetch_all_data, batch_update_all
from sources import TencentSource
from ai_chat import ai_chat_response
from tools.ai_adapter import detect_api_type
from tools.kline_chart import plot_kline
from tools.trend_analysis import judge_trend, generate_strategy_report, find_trend_turning_point
from tools.chat_history import (
    save_messages, load_messages, list_sessions,
    delete_session, format_summary_prompt,
)

_GRADIO_CSS = """
.log-box { border: 1px solid #ddd; border-radius: 6px; padding: 12px; background: #fafafa; font-size: 13px; min-height: 120px; max-height: 300px; overflow-y: auto; }
.status-ok { color: #16a34a; font-weight: bold; }
.status-info { color: #2563eb; }
footer { display: none !important; }
"""
_GRADIO_THEME = None


def ui_get_cached_choices():
    cached = StorageManager.list_cached()
    if not cached:
        return gr.update(choices=[], value=None)
    choices = []
    for c in cached:
        label = f"{c['code']} {c.get('name', '')} [{c.get('type', '?')}]"
        choices.append((label, c['code']))
    return gr.update(choices=choices, value=None)


def ui_refresh_cache_list():
    cached = StorageManager.list_cached()
    if not cached:
        return [["暂无缓存数据", "", "", "", "", ""]]
    rows = []
    for c in cached:
        name = c.get("name", "")
        atype = c.get("type", "")
        daily = c["types"].get("daily", {}).get("count", "-")
        m15 = c["types"].get("min15", {}).get("count", "-")
        m1 = c["types"].get("min1", {}).get("count", "-")
        rows.append([c["code"], name, atype,
                     f"{daily}条" if daily != "-" else "-",
                     f"{m15}条" if m15 != "-" else "-",
                     f"{m1}条" if m1 != "-" else "-"])
    return rows


def ui_refresh_delete_choices():
    cached = StorageManager.list_cached()
    choices = []
    for c in cached:
        label = f"{c['code']} {c.get('name', '')} [{c.get('type', '?')}]"
        choices.append((label, c['code']))
    return gr.update(choices=choices, value=None)


def create_ui():
    """构建 Gradio 多标签页 UI"""
    if gr is None:
        print("gradio 未安装: pip install gradio")
        import sys
        sys.exit(1)

    global _GRADIO_THEME
    if _GRADIO_THEME is None:
        _GRADIO_THEME = gr.themes.Soft()

    with gr.Blocks(title="A股ai助手", analytics_enabled=False) as app:

        gr.Markdown("""
        # A股ai助手
        **数据源**: mootdx+ 腾讯+新浪 + 东财 | **日K**: 2024-11-01之后 | **15min**: 最近3月 | **1min**: 最近3天
        **MA指标**: MA5/MA10/MA20 自动计算 | **AI模型**: 支持DeepSeek/ChatGPT/Claude/Gemini
        """)

        with gr.Tabs():
            # ════════════════════════════════════════
            # Tab 1: 数据获取
            # ════════════════════════════════════════
            with gr.TabItem("数据获取"):
                with gr.Row():
                    code_input = gr.Textbox(
                        label="股票代码", placeholder="输入6位股票代码。如果是指数或场内ETF在后方类型选择后再输",
                        scale=2, max_lines=1,
                    )
                    asset_type_selector = gr.Dropdown(
                        label="类型", scale=0,
                        choices=[("股票", "stock"), ("指数", "index"), ("ETF", "etf")],
                        value="stock",
                    )
                    asset_type_display = gr.Textbox(
                        label="识别", value="", interactive=False, scale=0, max_lines=1,
                        visible=False,
                    )

                with gr.Row():
                    fetch_daily = gr.Checkbox(label=DATE_RANGE["daily"]["label"], value=True)
                    fetch_min15 = gr.Checkbox(label=DATE_RANGE["min15"]["label"], value=True)
                    fetch_min1 = gr.Checkbox(label=DATE_RANGE["min1"]["label"], value=True)

                with gr.Row():
                    fetch_btn = gr.Button("获取并保存数据", variant="primary", scale=2)
                    check_btn = gr.Button("检查缓存状态", scale=1)

                with gr.Row():
                    with gr.Column(scale=1):
                        log_output = gr.HTML(label="执行日志")
                    with gr.Column(scale=1):
                        preview_output = gr.Dataframe(label="数据预览", wrap=False)

                def on_code_change(code):
                    code = normalize_code(code)
                    if len(code) == 6 and code.isdigit():
                        atype = detect_asset_type(code)
                        status = StorageManager.get_cache_status(code)
                        name = status.get("name", "")
                        type_names = {"stock": "股票", "index": "指数", "etf": "ETF"}
                        label = f"{type_names.get(atype, '?')} {name}"
                        cached_info = ""
                        if status["cached"]:
                            cached_info = " | 已缓存: "
                            parts = []
                            for kt, v in status["types"].items():
                                parts.append(f"{kt}={v['count']}条")
                            cached_info += ", ".join(parts)
                        return f"{label}{cached_info}"
                    return ""

                code_input.change(
                    fn=on_code_change,
                    inputs=[code_input],
                    outputs=[asset_type_display],
                )

                def do_fetch(code, daily, m15, m1, asset_type, progress=gr.Progress()):
                    code = normalize_code(code)
                    if not code or len(code) != 6 or not code.isdigit():
                        results = search_stock_by_name(code)
                        if len(results) == 0:
                            return "<div class='log-box'>未找到匹配的股票，请输入6位数字代码或完整名称</div>", None
                        elif len(results) == 1:
                            code = results[0]["code"]
                        else:
                            lines = ["找到多个匹配，请使用以下代码："]
                            for r in results:
                                lines.append(f"  {r['code']} {r['name']} [{r['type']}]")
                            return f"<div class='log-box'>{'<br>'.join(lines)}</div>", None
                    atype = {"stock": "stock", "index": "index", "etf": "etf"}.get(asset_type, None)
                    log_text = fetch_all_data(code, daily, m15, m1, progress, force_type=atype)
                    preview = None
                    for kt in ("daily", "min15", "min1"):
                        df = StorageManager.load_csv(code, kt, atype)
                        if not df.empty:
                            preview = df.head(10)
                            break
                    html_log = f'<div class="log-box"><pre style="margin:0">{log_text}</pre></div>'
                    return html_log, preview

                fetch_btn.click(
                    fn=do_fetch,
                    inputs=[code_input, fetch_daily, fetch_min15, fetch_min1,
                            asset_type_selector],
                    outputs=[log_output, preview_output],
                )

                def do_check(code):
                    code = normalize_code(code)
                    if not code or len(code) != 6 or not code.isdigit():
                        results = search_stock_by_name(code)
                        if not results:
                            return "<div class='log-box'>请输入代码或名称</div>", None
                        code = results[0]["code"]
                    status = StorageManager.get_cache_status(code)
                    if not status["cached"]:
                        return f"<div class='log-box'>{code} 暂无本地缓存</div>", None
                    name = status.get("name", "")
                    html = f"<div class='log-box'><b>{code} {name}</b><br>"
                    for kt, v in status["types"].items():
                        label = {"daily": "日K", "min15": "15分钟K", "min1": "1分钟K"}
                        html += (f"  {label.get(kt, kt)}: {v['count']}条 "
                                 f"(<b>{v['start']}</b> ~ <b>{v['end']}</b>)<br>")
                    html += "</div>"
                    preview = None
                    df = StorageManager.load_csv(code, "daily")
                    if not df.empty:
                        preview = df.head(10)
                    return html, preview

                check_btn.click(
                    fn=do_check,
                    inputs=[code_input],
                    outputs=[log_output, preview_output],
                )

            # ════════════════════════════════════════
            # Tab 2: 数据浏览 + K线图
            # ════════════════════════════════════════
            with gr.TabItem("数据浏览"):
                with gr.Row():
                    browse_code = gr.Dropdown(
                        label="股票代码", scale=2,
                        choices=[], interactive=True,
                        allow_custom_value=True,
                    )
                    refresh_codes_btn = gr.Button("刷新列表", scale=0)
                    browse_type = gr.Dropdown(
                        label="数据类型",
                        choices=[
                            ("日K线", "daily"),
                            ("15分钟K线", "min15"),
                            ("1分钟K线", "min1"),
                        ],
                        value="daily", scale=1,
                    )

                with gr.Row():
                    browse_rows = gr.Slider(
                        label="显示行数", minimum=10, maximum=1000,
                        value=50, step=10, scale=1,
                    )
                    browse_btn = gr.Button("加载数据", variant="primary", scale=0)

                kline_output = gr.Image(label="K线图 (含MA5/MA10/MA20)", type="filepath")
                browse_output = gr.Dataframe(label="数据表", wrap=False)
                browse_status = gr.HTML(label="状态")

                refresh_codes_btn.click(fn=ui_get_cached_choices, outputs=[browse_code])
                app.load(fn=ui_get_cached_choices, outputs=[browse_code])

                def do_browse(code, ktype, rows):
                    code = normalize_code(code)
                    if not code:
                        return None, "<div>请输入代码</div>", None

                    df = StorageManager.load_csv(code, ktype)
                    if df.empty:
                        return None, f"<div>{code} 无 {ktype} 数据</div>", None

                    df_show = df.head(rows)
                    cnt = len(df)
                    dc = get_ktype_col_date(ktype)
                    s = df[dc].iloc[-1] if not df.empty else "?"
                    e = df[dc].iloc[0] if not df.empty else "?"
                    html = (f"<div class='log-box'><b>{code}</b> "
                            f"共 {cnt} 条 | {s} ~ {e}</div>")

                    img_path = None
                    try:
                        status = StorageManager.get_cache_status(code)
                        name = status.get("name", "")
                        img_path = plot_kline(df, code=code, name=name, ktype=ktype,
                                              ma_periods=MA_PERIODS)
                    except Exception as e:
                        html += f"<br>图表生成失败: {e}"

                    return df_show, html, img_path if img_path else None

                browse_btn.click(
                    fn=do_browse,
                    inputs=[browse_code, browse_type, browse_rows],
                    outputs=[browse_output, browse_status, kline_output],
                )

            # ════════════════════════════════════════
            # Tab 3: AI 对话
            # ════════════════════════════════════════
            with gr.TabItem("AI 对话"):
                with gr.Row():
                    api_key_input = gr.Textbox(
                        label="API Key",
                        placeholder="sk-... 或 AIza... 等",
                        type="password", scale=2,
                    )
                    api_type_choices = [
                        ("DeepSeek", "deepseek"),
                        ("ChatGPT (OpenAI)", "openai"),
                        ("Claude (Anthropic)", "claude"),
                        ("Gemini (Google)", "gemini"),
                        ("OpenRouter", "openrouter"),
                        ("自定义 (OpenAI兼容)", "custom"),
                    ]
                    api_type_dropdown = gr.Dropdown(
                        label="API 类型", choices=api_type_choices,
                        value="deepseek", scale=1,
                    )
                    api_base_input = gr.Textbox(
                        label="Base URL", placeholder="留空使用默认",
                        scale=1,
                    )
                    model_input = gr.Textbox(
                        label="模型", placeholder="留空使用默认",
                        scale=1,
                    )

                with gr.Row():
                    chat_session_id = gr.Textbox(
                        label="会话ID", value="default",
                        scale=1, max_lines=1,
                    )
                    save_history_btn = gr.Button("保存聊天", scale=0)
                    load_history_btn = gr.Button("加载历史聊天", scale=0, visible=False)
                    summarize_btn = gr.Button("AI 总结聊天记录", scale=0, variant="primary", visible=False)

                chatbot = gr.Chatbot(label="对话", height=400)
                msg_input = gr.Textbox(
                    label="输入消息",
                    placeholder="例如：分析xx的行情走势。或：目前上升趋势的有哪些",
                    max_lines=3,
                )
                with gr.Row():
                    send_btn = gr.Button("发送", variant="primary", scale=0)
                    clear_chat_btn = gr.Button("清空对话", scale=0)

                chat_state = gr.State([])
                chat_status = gr.HTML(value="", visible=False)

                def detect_api_type_from_key(key):
                    detected = detect_api_type(key) if key else "deepseek"
                    return gr.update(value=detected)

                api_key_input.change(
                    fn=detect_api_type_from_key,
                    inputs=[api_key_input],
                    outputs=[api_type_dropdown],
                )

                def save_api_config(api_key, api_type, base_url, model):
                    save_config({
                        "ai_api_key": api_key,
                        "ai_api_type": api_type,
                        "ai_base_url": base_url,
                        "ai_model": model,
                    })

                api_key_input.blur(
                    fn=save_api_config,
                    inputs=[api_key_input, api_type_dropdown, api_base_input, model_input],
                    outputs=None,
                )

                def do_chat(message, history, api_key, api_type, base_url, model):
                    if not message or not message.strip():
                        return "", history, history, ""
                    if history is None:
                        history = []
                    response = ai_chat_response(
                        message, history, api_key, api_type, base_url, model
                    )
                    new_history = list(history)
                    new_history.append({"role": "user", "content": message})
                    new_history.append({"role": "assistant", "content": response})
                    return "", new_history, new_history, ""

                chat_inputs = [msg_input, chat_state, api_key_input,
                               api_type_dropdown, api_base_input, model_input]
                chat_outputs = [msg_input, chatbot, chat_state, chat_status]

                send_btn.click(fn=do_chat, inputs=chat_inputs, outputs=chat_outputs)
                msg_input.submit(fn=do_chat, inputs=chat_inputs, outputs=chat_outputs)

                def do_clear():
                    return [], []

                clear_chat_btn.click(fn=do_clear, outputs=[chatbot, chat_state])

                def load_api_config():
                    c = load_config()
                    api_key = c.get("ai_api_key", "") or c.get("deepseek_api_key", "")
                    return (api_key,
                            c.get("ai_api_type", "deepseek"),
                            c.get("ai_base_url", ""),
                            c.get("ai_model", ""))

                app.load(fn=load_api_config, outputs=[api_key_input, api_type_dropdown,
                         api_base_input, model_input])

                def do_save_history(history, session_id):
                    if not history:
                        return "<div class='log-box'>无内容可保存</div>"
                    save_messages(history, session_id)
                    return f"<div class='log-box'>已保存到会话 {session_id} ({len(history)} 条消息)</div>"

                save_history_btn.click(
                    fn=do_save_history,
                    inputs=[chat_state, chat_session_id],
                    outputs=[chat_status],
                )

                def do_load_history(session_id):
                    msgs = load_messages(session_id)
                    if not msgs:
                        return [], [], "<div class='log-box'>无历史记录</div>"
                    return msgs, msgs, f"<div class='log-box'>已加载 {len(msgs)} 条消息</div>"

                load_history_btn.click(
                    fn=do_load_history,
                    inputs=[chat_session_id],
                    outputs=[chatbot, chat_state, chat_status],
                )

                def do_summarize(history, api_key, api_type, base_url, model):
                    if not history or len(history) < 2:
                        return [], [], "<div class='log-box'>对话记录太短，无法总结</div>"
                    if not api_key:
                        return [], [], "<div class='log-box'>请先配置 API Key</div>"
                    chat_text = format_summary_prompt(history, max_history=50)
                    prompt = f"请总结以下A股分析对话，提炼关键信息（关注的股票、趋势判断、操作建议等）：\n\n{chat_text}"
                    try:
                        result = ai_chat_response(prompt, [], api_key, api_type, base_url, model)
                        summary_html = f"<div class='log-box'><b>对话总结</b><br><br>{result}</div>"
                        return history, history, summary_html
                    except Exception as e:
                        return history, history, f"<div class='log-box'>总结失败: {e}</div>"

                summarize_btn.click(
                    fn=do_summarize,
                    inputs=[chat_state, api_key_input, api_type_dropdown,
                            api_base_input, model_input],
                    outputs=[chatbot, chat_state, chat_status],
                )

            # ════════════════════════════════════════
            # Tab 4: 批量管理
            # ════════════════════════════════════════
            with gr.TabItem("批量管理"):
                refresh_btn = gr.Button("刷新缓存列表", variant="secondary")
                cache_list = gr.Dataframe(
                    label="已缓存的股票列表",
                    headers=["代码", "名称", "类型", "日K", "15分钟K", "1分钟K"],
                    wrap=False,
                )
                with gr.Row():
                    delete_selector = gr.Dropdown(
                        label="选择要删除的缓存",
                        choices=[], multiselect=True,
                        scale=3, interactive=True,
                    )
                    refresh_del_btn = gr.Button("刷新列表", scale=0)
                    delete_btn = gr.Button("删除选中", variant="stop", scale=0)

                with gr.Row():
                    batch_update_btn = gr.Button("批量更新所有缓存数据", variant="primary")
                batch_log = gr.HTML(label="批量更新日志")

                refresh_btn.click(fn=ui_refresh_cache_list, outputs=[cache_list])
                refresh_del_btn.click(fn=ui_refresh_delete_choices, outputs=[delete_selector])

                def do_delete(selected_codes):
                    if not selected_codes:
                        return "<div class='log-box'>请先选择要删除的代码</div>"
                    deleted = []
                    errors = []
                    for code in selected_codes:
                        d = get_data_dir(code)
                        if d.exists():
                            try:
                                shutil.rmtree(d)
                                deleted.append(code)
                            except Exception as e:
                                errors.append(f"{code}: {e}")
                    msg = ""
                    if deleted:
                        msg += f"已删除: {', '.join(deleted)}\n"
                    if errors:
                        msg += f"删除失败: {'; '.join(errors)}"
                    if not msg:
                        msg = "未执行任何删除"
                    return f'<div class="log-box"><pre style="margin:0">{msg}</pre></div>'

                delete_btn.click(
                    fn=do_delete,
                    inputs=[delete_selector],
                    outputs=[batch_log],
                ).then(
                    fn=ui_refresh_cache_list,
                    outputs=[cache_list],
                ).then(
                    fn=ui_refresh_delete_choices,
                    outputs=[delete_selector],
                )

                def do_batch_update(progress=gr.Progress()):
                    log_text = batch_update_all(progress)
                    return f'<div class="log-box"><pre style="margin:0">{log_text}</pre></div>'

                batch_update_btn.click(fn=do_batch_update, outputs=[batch_log])

                app.load(fn=ui_refresh_cache_list, outputs=[cache_list])
                app.load(fn=ui_refresh_delete_choices, outputs=[delete_selector])

            # ════════════════════════════════════════
            # Tab 5: 股票评估
            # ════════════════════════════════════════
            with gr.TabItem("股票评估"):
                gr.Markdown("""
                ### 股票趋势评估
                基于 MA5/MA10/MA20 均线排列 + 价格位置 + 趋势斜率的纯程序化综合判断。
                """)

                with gr.Row():
                    eval_mode = gr.Dropdown(
                        label="评估模式",
                        choices=[
                            ("批量评估（所有已缓存股票）", "batch"),
                            ("单个股票详细评估", "single"),
                        ],
                        value="batch", scale=1,
                    )

                with gr.Row(visible=False) as single_row:
                    eval_code = gr.Dropdown(
                        label="股票代码", scale=2,
                        choices=[], interactive=True,
                        allow_custom_value=True,
                    )
                    eval_refresh_btn = gr.Button("刷新列表", scale=0)
                    eval_btn = gr.Button("开始评估", variant="primary", scale=0)

                with gr.Row(visible=False) as single_result_row:
                    eval_trend = gr.Textbox(label="趋势判断", scale=1, interactive=False)
                    eval_confidence = gr.Textbox(label="置信度", scale=0, interactive=False)
                    eval_price = gr.Textbox(label="最新价", scale=0, interactive=False)

                eval_detail = gr.HTML(label="评估详情", visible=True)

                with gr.Row(visible=False) as ai_row:
                    eval_ai_concerns_btn = gr.Button("AI 关注事项", variant="primary")
                    eval_web_search = gr.Checkbox(
                        label="联网搜索 (DeepSeek)", value=False, scale=0,
                    )
                eval_ai_output = gr.HTML(label="AI 关注项")

                with gr.Row():
                    with gr.Accordion("查看趋势判断策略说明", open=False):
                        gr.Markdown(generate_strategy_report())
                        strategy_download_btn = gr.Button("下载策略说明")

                with gr.Row():
                    batch_sort_mode = gr.Dropdown(
                        label="排序方式", scale=0,
                        choices=[("按趋势", "趋势"), ("按代码", "代码"), ("按名称", "名称")],
                        value="趋势",
                    )
                    batch_eval_refresh_btn = gr.Button("刷新批量评估", variant="primary", scale=0)

                def on_mode_change(mode, code, sort_mode="趋势"):
                    single_visible = mode == "single"
                    if mode == "batch":
                        detail = do_batch_evaluate(sort_mode)
                    elif mode == "single" and code:
                        *_, detail = do_single_evaluate(code)
                    else:
                        detail = "<div class='log-box'>请选择股票代码后点击开始评估</div>"
                    return (
                        gr.update(visible=single_visible),
                        gr.update(visible=single_visible),
                        gr.update(visible=single_visible),
                        detail,
                    )

                eval_mode.change(
                    fn=on_mode_change,
                    inputs=[eval_mode, eval_code, batch_sort_mode],
                    outputs=[single_row, single_result_row, ai_row, eval_detail],
                )

                eval_refresh_btn.click(fn=ui_get_cached_choices, outputs=[eval_code])
                app.load(fn=ui_get_cached_choices, outputs=[eval_code])

                def do_batch_evaluate(sort_mode="趋势"):
                    cached = StorageManager.list_cached()
                    if not cached:
                        return "<div class='log-box'>暂无缓存数据</div>"

                    rows = []
                    for c in cached:
                        code = c["code"]
                        name = c.get("name", "")
                        df = StorageManager.load_csv(code, "daily")
                        if df.empty or len(df) < 20:
                            rows.append([code, name, "数据不足", "-", "-", "-", False, ""])
                            continue

                        result = judge_trend(df)
                        d = result["detail"]
                        trend_icon = {"up": "上涨", "down": "下跌", "range": "震荡"}
                        trend = trend_icon.get(result["trend"], "?")
                        conf = f"{result['confidence']*100:.0f}%"
                        price = f"{d.get('latest_close', 0):.2f}"

                        tp = find_trend_turning_point(df)
                        if tp["had_turning"]:
                            change_date = tp["change_date"]
                            days_since = tp["days_since_change"]
                            prev_raw = tp.get("prev_trend", "")
                            short_prev = prev_raw.replace("趋势", "").replace(" ", "")
                            change_info = f"{short_prev} ({change_date})"
                            is_new = days_since <= 2
                        else:
                            change_info = "持续"
                            is_new = False

                        rows.append([code, name, trend, conf, price, change_info, is_new,
                                     tp.get("current_trend", "")])

                    if sort_mode == "趋势":
                        sort_key = {"上涨": 0, "震荡": 1, "下跌": 2}
                        rows.sort(key=lambda r: (sort_key.get(r[2], 99), r[0]))
                    elif sort_mode == "代码":
                        rows.sort(key=lambda r: r[0])
                    elif sort_mode == "名称":
                        rows.sort(key=lambda r: r[1])

                    table_html = """<div class="log-box" style="max-height:500px;overflow-y:auto;">
                    <table style="width:100%; border-collapse: collapse; font-size: 13px;">
                    <thead><tr style="background:#e8e8e8;">
                        <th style="padding:6px 8px;text-align:left;">代码</th>
                        <th style="padding:6px 8px;text-align:left;">名称</th>
                        <th style="padding:6px 8px;text-align:left;">趋势</th>
                        <th style="padding:6px 8px;text-align:center;">置信度</th>
                        <th style="padding:6px 8px;text-align:right;">最新价</th>
                        <th style="padding:6px 8px;text-align:left;">上次拐点</th>
                    </tr></thead><tbody>
                    """
                    for r in rows:
                        color = "#ef5350" if "上涨" in r[2] else "#26a69a" if "下跌" in r[2] else "#f39c12"
                        cell_style = ' style="background:#fff3cd;font-weight:bold;"' if r[6] else ""
                        table_html += f"""<tr style="border-top:1px solid #ddd;">
                            <td style="padding:4px 8px;">{r[0]}</td>
                            <td style="padding:4px 8px;">{r[1]}</td>
                            <td style="padding:4px 8px;color:{color};font-weight:bold;">{r[2]}</td>
                            <td style="padding:4px 8px;text-align:center;">{r[3]}</td>
                            <td style="padding:4px 8px;text-align:right;">{r[4]}</td>
                            <td style="padding:4px 8px;text-align:left;{cell_style}">{r[5]}</td>
                        </tr>"""
                    table_html += "</tbody></table></div>"
                    return table_html

                batch_eval_refresh_btn.click(
                    fn=do_batch_evaluate,
                    inputs=[batch_sort_mode],
                    outputs=[eval_detail],
                )
                batch_sort_mode.change(
                    fn=do_batch_evaluate,
                    inputs=[batch_sort_mode],
                    outputs=[eval_detail],
                )

                def do_export_batch():
                    cached = StorageManager.list_cached()
                    if not cached:
                        return "<div class='log-box'>暂无缓存数据可导出</div>"
                    rows = [["代码", "名称", "趋势", "置信度", "最新价", "上次拐点"]]
                    for c in cached:
                        code = c["code"]
                        name = c.get("name", "")
                        df = StorageManager.load_csv(code, "daily")
                        if df.empty or len(df) < 20:
                            rows.append([code, name, "数据不足", "", "", ""])
                            continue
                        result = judge_trend(df)
                        trend = {"up": "上涨", "down": "下跌", "range": "震荡"}.get(result["trend"], "?")
                        conf = f"{result['confidence']*100:.0f}%"
                        price = f"{result['detail']['latest_close']:.2f}"
                        tp = find_trend_turning_point(df)
                        change = tp.get("change_date", "") if tp.get("had_turning") else "持续"
                        rows.append([code, name, trend, conf, price, change])
                    output = io.StringIO()
                    writer = csv.writer(output)
                    writer.writerows(rows)
                    save_path = DATA_ROOT / f"batch_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                    save_path.write_text(output.getvalue(), encoding="utf-8-sig")
                    return f"<div class='log-box'>已导出: {save_path} ({len(rows)-1} 条)</div>"

                with gr.Row():
                    export_batch_btn = gr.Button("导出批量评估 CSV", scale=0)
                    export_batch_btn.click(fn=do_export_batch, outputs=[eval_detail])

                def do_single_evaluate(code):
                    code = normalize_code(code)
                    if not code or len(code) != 6 or not code.isdigit():
                        return ("请输入有效代码", "", "", "")
                    status = StorageManager.get_cache_status(code)
                    name = status.get("name", "")
                    df = StorageManager.load_csv(code, "daily")
                    if df.empty:
                        return ("无日K数据，请先在数据获取标签页获取数据", "", "", "")
                    result = judge_trend(df)
                    trend = result["trend_label"]
                    confidence = f"{result['confidence']*100:.0f}%"
                    latest = result["detail"].get("latest_close", 0)
                    detail = result["detail"]
                    html = f"""
                    <div class="log-box">
                    <b>{code} {name}</b><br><br>
                    <b>趋势判断:</b> {trend} (置信度 {confidence})<br>
                    <b>最新收盘价:</b> {detail.get('latest_close', 0):.2f}<br>
                    <b>MA5:</b> {detail.get('ma5', 0):.2f} |
                    <b>MA10:</b> {detail.get('ma10', 0):.2f} |
                    <b>MA20:</b> {detail.get('ma20', 0):.2f}<br><br>
                    <b>均线多头排列比率:</b> {detail.get('ma5_good_ratio', 0)}<br>
                    <b>均线空头排列比率:</b> {detail.get('ma5_bad_ratio', 0)}<br>
                    <b>价格在MA20上方:</b> {'是' if detail.get('price_above_ma20') else '否'}<br>
                    <b>趋势斜率:</b> {detail.get('slope_pct', 0):.2f}%/bar<br>
                    <b>MA5-MA20差值:</b> {detail.get('ma5_ma20_diff', 0):.4f}<br>
                    </div>
                    """
                    return trend, confidence, f"{latest:.2f}", html

                def do_evaluate(code, mode):
                    if mode == "batch":
                        return do_batch_evaluate()
                    else:
                        *_, detail_html = do_single_evaluate(code)
                        return detail_html

                eval_btn.click(
                    fn=do_evaluate,
                    inputs=[eval_code, eval_mode],
                    outputs=[eval_detail],
                )

                def do_export_single(code):
                    code = normalize_code(code)
                    if not code or len(code) != 6 or not code.isdigit():
                        return "<div class='log-box'>请输入有效代码</div>"
                    status = StorageManager.get_cache_status(code)
                    name = status.get("name", "")
                    df = StorageManager.load_csv(code, "daily")
                    lines = [f"股票评估报告 - {code} {name}",
                             f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
                    if df.empty:
                        lines.append("数据不足")
                    else:
                        result = judge_trend(df)
                        d = result["detail"]
                        lines.append(f"趋势: {result['trend_label']} (置信度 {result['confidence']*100:.0f}%)")
                        lines.append(f"最新收盘: {d['latest_close']:.2f}")
                        lines.append(f"MA5: {d['ma5']:.2f}  MA10: {d['ma10']:.2f}  MA20: {d['ma20']:.2f}")
                        lines.append(f"趋势斜率: {d['slope_pct']:.2f}%/bar")
                        tp = find_trend_turning_point(df)
                        if tp.get("had_turning"):
                            lines.append(f"上次拐点: {tp['change_date']} ({tp['prev_trend']})")
                        lines.append("")
                        lines.append("--- 策略说明 ---")
                        lines.append(generate_strategy_report())
                    save_path = DATA_ROOT / f"eval_{code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                    save_path.write_text("\n".join(lines), encoding="utf-8")
                    return f"<div class='log-box'>已导出: {save_path}</div>"

                with gr.Row():
                    export_single_btn = gr.Button("导出评估报告", scale=0)
                    export_single_btn.click(fn=do_export_single, inputs=[eval_code],
                                            outputs=[eval_detail])

                def do_ai_concerns(code, api_key, api_type, base_url, model, web_search=False):
                    code = normalize_code(code)
                    if not code:
                        return "<div class='log-box'>请输入股票代码</div>"
                    if not api_key:
                        return "<div class='log-box'>请先在 AI 对话标签页配置 API Key</div>"
                    status = StorageManager.get_cache_status(code)
                    name = status.get("name", "")
                    df = StorageManager.load_csv(code, "daily")
                    summary_parts = [f"股票: {code} {name}"]
                    if not df.empty:
                        trend_result = judge_trend(df)
                        d = trend_result["detail"]
                        summary_parts.append(
                            f"趋势: {trend_result['trend_label']}, "
                            f"最新价: {d.get('latest_close', 0):.2f}, "
                            f"MA5: {d.get('ma5', 0):.2f}, "
                            f"MA10: {d.get('ma10', 0):.2f}, "
                            f"MA20: {d.get('ma20', 0):.2f}, "
                            f"斜率: {d.get('slope_pct', 0):.2f}%/bar"
                        )
                        recent = df.head(5)
                        summary_parts.append(
                            f"最近5条数据:\n{recent[['date','close','volume','amount']].to_string(index=False)}"
                        )
                    realtime = TencentSource.get_realtime(code)
                    if "error" not in realtime:
                        summary_parts.append(
                            f"实时行情: 涨跌{realtime.get('change_pct', 0):.2f}%, "
                            f"换手{realtime.get('turnover_pct', 0):.2f}%, "
                            f"PE={realtime.get('pe_ttm', 0)}, PB={realtime.get('pb', 0)}"
                        )
                    prompt = (
                        "你是一个A股分析助手。请基于以下数据，用简短的文字列出该股票当前需要关注的要点。"
                        "只包括必要的：\n"
                        "1. **异动提示**: 价格异常波动、成交量变化\n"
                        "2. **资金流动**: 主力资金动向（如有数据）\n"
                        "3. **风险警示**: 技术面风险、价格破位等\n\n"
                        "3. **其他评估**: 结合市场行情、行业及国内外重要新闻等给出的提示\n\n"
                        "如果数据不足，只列出你能判断的项目。保持简洁，每条不超过三句话。"
                        "不要编造数据。\n\n"
                        + "\n".join(summary_parts)
                    )
                    try:
                        final_prompt = ("[联网] " if web_search else "") + prompt
                        result = ai_chat_response(
                            final_prompt, [], api_key, api_type, base_url, model
                        )
                        return f'<div class="log-box"><pre style="margin:0">{result}</pre></div>'
                    except Exception as e:
                        return f"<div class='log-box'>AI 调用失败: {e}</div>"

                eval_ai_concerns_btn.click(
                    fn=do_ai_concerns,
                    inputs=[eval_code, api_key_input, api_type_dropdown,
                            api_base_input, model_input, eval_web_search],
                    outputs=[eval_ai_output],
                )

                def save_strategy_report():
                    report = generate_strategy_report()
                    save_path = Path(__file__).parent / "趋势判断策略说明.md"
                    save_path.write_text(report, encoding="utf-8")
                    return f"<div class='log-box'>已保存到: {save_path}</div>"

                strategy_download_btn.click(
                    fn=save_strategy_report,
                    outputs=[eval_ai_output],
                )

    return app
