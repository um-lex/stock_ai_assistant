#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 多模型兼容适配层 — 自动兼容 DeepSeek / ChatGPT / Claude / Gemini 等。

检测规则
--------
- sk-{随机字符}             → OpenAI / DeepSeek / 兼容格式（需指定 base_url）
- sk-ant-{字符}             → Anthropic Claude
- AIza{字符}                → Google Gemini

支持通过 UI 手动选择 API 类型和 base_url。
"""

import json
import re
from typing import Optional


# ── 已知 API 端点 ──────────────────────────────────────────────
API_ENDPOINTS = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "key_pattern": r"^sk-[a-zA-Z0-9]{20,}$",
    },
    "openai": {
        "label": "ChatGPT (OpenAI)",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
        "key_pattern": r"^sk-[a-zA-Z0-9]{20,}$",
    },
    "claude": {
        "label": "Claude (Anthropic)",
        "base_url": "https://api.anthropic.com/v1",
        "models": ["claude-sonnet-4-20250514", "claude-3-5-sonnet-latest", "claude-3-opus-latest"],
        "key_pattern": r"^sk-ant-[a-zA-Z0-9]{10,}$",
    },
    "gemini": {
        "label": "Gemini (Google)",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "models": ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"],
        "key_pattern": r"^AIza[A-Za-z0-9_-]{10,}$",
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "models": ["openai/gpt-4o", "anthropic/claude-sonnet-4", "google/gemini-2.0-flash"],
        "key_pattern": r"^sk-or-[a-zA-Z0-9]{20,}$",
    },
    "custom": {
        "label": "自定义 (OpenAI兼容)",
        "base_url": "",
        "models": [],
        "key_pattern": r".+",
    },
}


def detect_api_type(api_key: str) -> str:
    """根据 API Key 格式自动检测 API 类型，返回 key (如 'deepseek')。"""
    if not api_key:
        return "deepseek"
    for key, cfg in API_ENDPOINTS.items():
        if key == "custom":
            continue
        if re.match(cfg["key_pattern"], api_key.strip()):
            return key
    # 默认按 OpenAI 兼容处理
    return "openai"


def get_supported_models(api_type: str) -> list:
    """获取指定 API 类型的推荐模型列表。"""
    cfg = API_ENDPOINTS.get(api_type)
    if not cfg:
        return []
    return cfg.get("models", [])


def init_client(api_key: str, api_type: str = None, base_url: str = None,
                model: str = None):
    """
    初始化对应的 API 客户端。

    返回
    ----
    (client, actual_model, actual_base_url)
    """
    from openai import OpenAI

    if api_type is None:
        api_type = detect_api_type(api_key)
    if api_type == "custom":
        api_type = "openai"  # 自定义用 OpenAI 兼容协议

    cfg = API_ENDPOINTS.get(api_type, API_ENDPOINTS["openai"])
    actual_base_url = base_url or cfg.get("base_url", "")

    # Claude 和 Gemini 走各自的 SDK，其通过 OpenAI兼容模式
    # Claude: 通过 Anthropic SDK 或 OpenRouter
    # Gemini: 通过 Google AI SDK 或 OpenRouter
    # 这里统一用 OpenAI 兼容协议：Claude/Gemini 也可通过 OpenRouter 调用
    # 如果用户直接使用 Claude/Gemini 原生 API，使用对应 SDK

    if api_type == "claude" and "anthropic" in actual_base_url:
        # Anthropic Claude 原生 API
        try:
            from anthropic import Anthropic
        except ImportError:
            raise ImportError("使用 Claude 原生 API 需要安装: pip install anthropic")
        client = Anthropic(api_key=api_key)
        actual_model = model or "claude-sonnet-4-20250514"
        return client, actual_model, actual_base_url, "anthropic"

    if api_type == "gemini" and "googleapis" in actual_base_url:
        # Google Gemini 原生 API
        try:
            import google.generativeai as genai
        except ImportError:
            raise ImportError("使用 Gemini 原生 API 需要安装: pip install google-generativeai")
        genai.configure(api_key=api_key)
        actual_model = model or "gemini-2.0-flash"
        return genai, actual_model, actual_base_url, "gemini"

    # 默认: OpenAI 兼容协议 (DeepSeek, OpenAI, OpenRouter, 自定义)
    client = OpenAI(api_key=api_key, base_url=actual_base_url)
    actual_model = model or cfg.get("models", [None])[0] or "gpt-4o"
    return client, actual_model, actual_base_url, "openai"


def chat_completion(client, model: str, messages: list, tools_def: list = None,
                    api_protocol: str = "openai", tool_choice: str = "auto",
                    max_turns: int = 5):
    """
    统一聊天补全接口，屏蔽不同 SDK 的调用差异。

    为简化多协议兼容，Claude/Gemini 推荐走 OpenRouter 的 OpenAI 兼容格式，
    这样所有模型都用同一套代码路径。
    """
    if api_protocol in ("openai",):
        return _chat_openai(client, model, messages, tools_def, tool_choice, max_turns)
    elif api_protocol == "anthropic":
        return _chat_anthropic(client, model, messages, tools_def, tool_choice, max_turns)
    elif api_protocol == "gemini":
        return _chat_gemini(client, model, messages, tools_def, tool_choice, max_turns)
    else:
        return _chat_openai(client, model, messages, tools_def, tool_choice, max_turns)


def _chat_openai(client, model, messages, tools_def, tool_choice, max_turns):
    """OpenAI 兼容协议聊天（DeepSeek / OpenAI / OpenRouter 等）。"""
    for turn in range(max_turns):
        kwargs = dict(model=model, messages=messages)
        if tools_def:
            kwargs["tools"] = tools_def
            kwargs["tool_choice"] = tool_choice
        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as e:
            return {"error": str(e), "content": None}

        msg = response.choices[0].message

        if msg.tool_calls:
            tc_list = [
                {"id": tc.id, "function": tc.function, "type": "function"}
                for tc in msg.tool_calls
            ]
            messages.append({
                "role": "assistant",
                "content": msg.content or None,
                "tool_calls": tc_list,
            })
            for tc in msg.tool_calls:
                yield {"type": "tool_call", "name": tc.function.name,
                       "arguments": tc.function.arguments, "tool_call_id": tc.id}
            continue

        content = (msg.content or "").strip()
        yield {"type": "text", "content": content}
        return

    yield {"type": "text", "content": "处理完成"}


def _chat_anthropic(client, model, messages, tools_def, tool_choice, max_turns):
    """Anthropic Claude 原生 SDK。"""
    # 转换消息格式: OpenAI → Anthropic
    system_content = None
    anthro_messages = []
    for m in messages:
        if m["role"] == "system":
            system_content = m["content"]
            continue
        role = "assistant" if m["role"] == "assistant" else "user"
        anthro_messages.append({"role": role, "content": m.get("content", "")})

    try:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_content,
            messages=anthro_messages,
        )
        content = response.content[0].text if response.content else ""
        yield {"type": "text", "content": content}
    except Exception as e:
        yield {"type": "error", "content": str(e)}


def _chat_gemini(client, model, messages, tools_def, tool_choice, max_turns):
    """Google Gemini 原生 SDK。"""
    try:
        genai = client
        gemini_model = genai.GenerativeModel(model)
        # 转换消息: 取最后一条 user 消息
        user_text = ""
        for m in reversed(messages):
            if m["role"] == "user":
                user_text = m["content"]
                break
        response = gemini_model.generate_content(user_text)
        yield {"type": "text", "content": response.text}
    except Exception as e:
        yield {"type": "error", "content": str(e)}
