#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
聊天记录管理模块 — 保存/加载/总结聊天历史。
"""

import os
import json
from datetime import datetime
from pathlib import Path


CHAT_DIR = Path(__file__).parent.parent / "data" / "chat_history"


def ensure_chat_dir():
    """确保聊天记录目录存在。"""
    CHAT_DIR.mkdir(parents=True, exist_ok=True)


def get_session_path(session_id: str = None) -> Path:
    """获取当前会话记录路径。如不指定 session_id，使用今天的日期文件。"""
    ensure_chat_dir()
    if session_id is None:
        session_id = datetime.now().strftime("%Y%m%d")
    return CHAT_DIR / f"session_{session_id}.json"


def save_messages(messages: list, session_id: str = None):
    """保存聊天消息列表到本地文件。"""
    path = get_session_path(session_id)
    ensure_chat_dir()
    for m in messages:
        if "timestamp" not in m:
            m["timestamp"] = datetime.now().isoformat()
    path.write_text(json.dumps(messages, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def load_messages(session_id: str = None) -> list:
    """加载指定会话的聊天记录。"""
    path = get_session_path(session_id)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, Exception):
        return []


def list_sessions() -> list[dict]:
    """列出所有本地聊天会话。"""
    ensure_chat_dir()
    sessions = []
    for p in sorted(CHAT_DIR.glob("session_*.json"), reverse=True):
        session_id = p.stem.replace("session_", "")
        size = p.stat().st_size
        mod_time = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        sessions.append({
            "session_id": session_id,
            "file": str(p),
            "size": size,
            "modified": mod_time,
        })
    return sessions


def delete_session(session_id: str) -> bool:
    """删除指定会话记录。"""
    path = get_session_path(session_id)
    if path.exists():
        path.unlink()
        return True
    return False


def format_summary_prompt(messages: list, max_history: int = 100) -> str:
    """
    生成用于 AI 总结聊天记录的 prompt。
    将最近的聊天记录整理为可发送给 AI 的格式。
    """
    recent = messages[-max_history:] if len(messages) > max_history else messages
    lines = []
    for m in recent:
        role = m.get("role", "?")
        content = m.get("content", "")
        if len(content) > 200:
            content = content[:200] + "..."
        lines.append(f"[{role}] {content}")
    return "\n".join(lines)
