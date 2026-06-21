"""AI 对话：函数定义、函数执行、AI聊天"""

import json

from config import OpenAI
from sources import MootdxSource, TencentSource, EastMoneySource
from storage import StorageManager, fetch_kline_incremental
from tools.ai_adapter import (
    detect_api_type, API_ENDPOINTS, get_supported_models,
    chat_completion, init_client,
)

FUNCTIONS_DEF = [
    {
        "name": "get_kline",
        "description": "获取股票/指数/ETF的K线数据，支持日K/15分钟/1分钟",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "6位股票代码"},
                "ktype": {"type": "string", "enum": ["daily", "min15", "min1"],
                          "description": "K线类型: daily=日K, min15=15分钟, min1=1分钟"},
                "limit": {"type": "integer", "description": "返回最近N条，默认30"},
            },
            "required": ["code", "ktype"],
        }
    },
    {
        "name": "get_realtime",
        "description": "获取股票/指数/ETF的实时行情（最新价、涨跌幅、PE/PB/市值等）",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "6位股票代码"},
            },
            "required": ["code"],
        }
    },
    {
        "name": "get_stock_info_local",
        "description": "查看本地已缓存的股票数据情况",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "6位股票代码"},
            },
            "required": ["code"],
        }
    },
    {
        "name": "get_cache_status",
        "description": "列出所有本地已缓存数据的股票",
        "parameters": {
            "type": "object",
            "properties": {},
        }
    },
    {
        "name": "get_fund_flow",
        "description": "获取个股资金流向（主力/散户净流入），支持分钟级和日级",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "6位股票代码"},
                "type": {"type": "string", "enum": ["minute", "daily"],
                         "description": "minute=当日分钟级, daily=近120日"},
            },
            "required": ["code", "type"],
        }
    },
    {
        "name": "get_concept_blocks",
        "description": "获取个股所属板块/概念归属",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "6位股票代码"},
            },
            "required": ["code"],
        }
    },
    {
        "name": "fetch_stock_data",
        "description": "获取并保存股票最新数据到本地缓存（日K+15分钟+1分钟）",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "6位股票代码"},
            },
            "required": ["code"],
        }
    },
]


def execute_function(name: str, args: dict) -> str:
    """执行函数调用并返回结果字符串"""
    try:
        if name == "get_kline":
            code = args["code"]
            ktype = args["ktype"]
            limit = int(args.get("limit", 30))
            local_df = StorageManager.load_csv(code, ktype)
            if not local_df.empty:
                df = local_df.head(limit)
            elif ktype == "daily":
                df = MootdxSource.fetch_kline(code, ktype)
                df = df.head(limit) if not df.empty else df
            else:
                df = TencentSource.fetch_mkline(code, ktype)
                df = df.head(limit) if not df.empty else df
            if df.empty:
                return "暂无数据"
            return df.to_string(index=False)

        elif name == "get_realtime":
            code = args["code"]
            info = TencentSource.get_realtime(code)
            if "error" in info:
                em_info = EastMoneySource.stock_info(code)
                if em_info:
                    return json.dumps(em_info, ensure_ascii=False)
                return info["error"]
            return json.dumps(info, ensure_ascii=False, indent=2)

        elif name == "get_stock_info_local":
            code = args["code"]
            status = StorageManager.get_cache_status(code)
            return json.dumps(status, ensure_ascii=False, indent=2)

        elif name == "get_cache_status":
            cached = StorageManager.list_cached()
            if not cached:
                return "暂无缓存数据"
            summary = []
            for c in cached:
                t = c.get("type", "?")
                n = c.get("name", "")
                types_info = "; ".join(
                    f"{kt}: {v['count']}条({v['start']}~{v['end']})"
                    for kt, v in c["types"].items()
                )
                summary.append(f"[{t}] {c['code']} {n} | {types_info}")
            return "\n".join(summary)

        elif name == "get_fund_flow":
            code = args["code"]
            ftype = args.get("type", "daily")
            if ftype == "minute":
                data = EastMoneySource.fund_flow_minute(code)
            else:
                data = EastMoneySource.fund_flow_120d(code)
            if not data:
                return "暂无资金流数据"
            if ftype == "daily":
                recent = data[-20:]
                total_main = sum(d["main_net"] for d in recent)
                summary = (f"近20日主力累计净流入: {total_main/1e8:.2f}亿\n"
                           f"最近5日:\n")
                for d in data[-5:]:
                    summary += f"  {d['date']}: 主力={d['main_net']/1e4:.0f}万 超大单={d['super_net']/1e4:.0f}万\n"
                return summary
            else:
                if data:
                    last = data[-1]
                    return (f"当日主力累计净流入: {last['main_net']/1e4:.0f}万\n"
                            f"数据点数: {len(data)}")
                return "暂无当日资金流"

        elif name == "get_concept_blocks":
            code = args["code"]
            blocks = EastMoneySource.concept_blocks(code)
            if not blocks["boards"]:
                return "暂无板块归属数据"
            result = f"共 {blocks['total']} 个板块:\n"
            for b in blocks["boards"][:15]:
                result += f"  {b['name']} (BK{b['code']}) 涨跌{b['change_pct']}%\n"
            if blocks["total"] > 15:
                result += f"  ... 等共 {blocks['total']} 个\n"
            return result

        elif name == "fetch_stock_data":
            code = args["code"]
            logs = []
            for kt in ("daily", "min15", "min1"):
                _, msg = fetch_kline_incremental(code, kt)
                logs.append(f"{kt}: {msg}")
            return "\n".join(logs)

        else:
            return f"未知函数: {name}"
    except Exception as e:
        return f"函数执行错误 [{name}]: {e}"


def init_ai_client(api_key: str, api_type: str, base_url: str, model: str):
    from tools.ai_adapter import init_client
    try:
        return init_client(api_key, api_type, base_url, model)
    except ImportError as e:
        raise e


def ai_chat_response(message: str, history: list, api_key: str,
                     api_type: str = "deepseek", base_url: str = "",
                     model: str = None) -> str:
    """AI 对话响应（多模型兼容）"""
    if not api_key:
        return "请先输入 API Key"
    if not OpenAI:
        return "openai 库未安装: pip install openai"

    try:
        client, actual_model, actual_base_url, protocol = init_ai_client(
            api_key, api_type, base_url, model
        )
    except ImportError as e:
        return str(e)
    except Exception as e:
        return f"客户端初始化失败: {e}"

    if protocol != "openai":
        try:
            gen = chat_completion(client, actual_model,
                   [{"role": "user", "content": message}], None, protocol,
                   tool_choice="none", max_turns=1)
            for chunk in gen:
                if chunk["type"] == "text":
                    return chunk["content"]
                elif chunk["type"] == "error":
                    return f"AI 回复失败: {chunk['content']}"
            return "（无回复）"
        except Exception as e:
            return f"AI 调用失败: {e}"

    tools_def = [{"type": "function", "function": f} for f in FUNCTIONS_DEF]

    msgs = [
        {"role": "system", "content": (
            "你是A股分析助手。当用户问及股票数据时，你必须使用提供的函数来获取数据，"
            "不要编造数据，不要以文本形式输出函数调用描述。"
            "可用的函数包括：获取K线、实时行情、本地缓存、资金流向、板块归属、数据获取。\n\n"
            "回答格式：先调用函数获取数据，然后基于数据给出分析。"
        )}
    ]

    for h in history[-40:]:
        msgs.append({"role": h.get("role", "user"), "content": h.get("content", "")})
    msgs.append({"role": "user", "content": message})

    for turn in range(5):
        try:
            kwargs = dict(model=actual_model, messages=msgs,
                          tools=tools_def, tool_choice="auto")
            if api_type == "deepseek" and message.startswith("[联网]"):
                kwargs["extra_body"] = {"enable_search": True}
            response = client.chat.completions.create(**kwargs)
        except Exception as e:
            return f"API 调用失败: {e}"

        msg = response.choices[0].message

        if msg.tool_calls:
            tc_list = [
                {"id": tc.id, "function": tc.function, "type": "function"}
                for tc in msg.tool_calls
            ]
            msgs.append({
                "role": "assistant",
                "content": msg.content or None,
                "tool_calls": tc_list,
            })
            for tc in msg.tool_calls:
                func_name = tc.function.name
                raw_args = tc.function.arguments
                if isinstance(raw_args, str):
                    try:
                        func_args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        func_args = {}
                else:
                    func_args = raw_args
                result = execute_function(func_name, func_args)
                msgs.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(result),
                })
            continue

        content = (msg.content or "").strip()
        if content:
            if any(kw in content for kw in ("get_kline(", "get_realtime(",
                   "fetch_stock(", "get_fund(", "get_concept(")):
                msgs.append({
                    "role": "user",
                    "content": "请直接调用函数获取数据，不要输出函数调用的文本。"
                })
                continue
            return content
        continue

    msgs.append({"role": "user", "content": "请基于已有数据给出最终结论，不要再调用函数。"})
    try:
        kwargs = dict(model=actual_model, messages=msgs,
                      tools=tools_def, tool_choice="none")
        if api_type == "deepseek":
            kwargs["extra_body"] = {"enable_search": True}
        final = client.chat.completions.create(**kwargs)
        return final.choices[0].message.content or "（分析完成）"
    except Exception as e:
        return f"最终回复失败: {e}"
