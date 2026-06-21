#!/usr/bin/env python3
"""A股ai助手 — 入口"""

import os
import sys
from datetime import datetime
from pathlib import Path

from config import DATA_ROOT, CONFIG_PATH, _TYPE_DIR_MAP, detect_asset_type


def check_dependencies():
    deps = {
        "mootdx": ("from mootdx.quotes import Quotes", "K线数据"),
        "gradio": ("import gradio as gr", "图形界面"),
        "openai": ("from openai import OpenAI", "AI对话(可选)"),
    }

    python_exe = sys.executable
    python_ver = sys.version.split()[0]

    missing = []
    errors = []

    for name, (import_stmt, purpose) in deps.items():
        try:
            exec(import_stmt)
        except ImportError as e:
            missing.append(f"{name} ({purpose})")
            errors.append(f"  {name}: ImportError — {e}")
        except Exception as e:
            errors.append(f"  {name}: {type(e).__name__} — {e}")

    if "openai" in [m.split()[0] for m in missing]:
        missing = [m for m in missing if not m.startswith("openai")]

    if missing:
        print("\n" + "=" * 56)
        print("  依赖缺失")
        print("=" * 56)
        for m in missing:
            pkg_name = m.split()[0]
            print(f"  {m}")
            print(f"     安装: pip install {pkg_name}")
        if errors:
            print("\n  详细错误信息:")
            for e in errors:
                print(e)
        print(f"\n  Python: {python_exe}")
        print(f"  版本: {python_ver}")
        print("=" * 56 + "\n")
        return False

    if errors:
        print("\n  导入警告:")
        for e in errors:
            print(f"  {e}")

    return True


def check_duplicate_dirs():
    dir_to_type = {v: k for k, v in _TYPE_DIR_MAP.items()}
    code_dirs = {}
    for sub_dir in dir_to_type:
        p = DATA_ROOT / sub_dir
        if not p.exists():
            continue
        for d in list(p.iterdir()):
            if d.is_dir():
                code_dirs.setdefault(d.name, []).append((sub_dir, d))

    warnings_found = []
    for code, entries in code_dirs.items():
        if len(entries) <= 1:
            continue
        correct_type = detect_asset_type(code)
        correct_dir = _TYPE_DIR_MAP.get(correct_type, "stocks")
        for sub_dir, d in entries:
            if sub_dir != correct_dir:
                warnings_found.append(f"  {code}: 同时存在于 {sub_dir}/ 和 {correct_dir}/，应删除 {sub_dir}/{code}")
    return warnings_found


def main():
    os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except AttributeError:
            pass

    print("=" * 56)
    print("  A股ai助手")
    print("  A股K线获取 | AI分析和对话 | 趋势评估")
    print("=" * 56)

    if not check_dependencies():
        sys.exit(1)

    DATA_ROOT.mkdir(parents=True, exist_ok=True)

    dup_warnings = check_duplicate_dirs()
    if dup_warnings:
        print("\n发现重复分类目录，请手动删除：")
        for w in dup_warnings:
            print(f"  {w}")
        print()

    print(f"数据目录: {DATA_ROOT}")

    from app import create_ui, _GRADIO_CSS, _GRADIO_THEME

    app = create_ui()
    app.launch(
        server_name="127.0.0.1",
        server_port=7860,
        share=False,
        show_error=True,
        inbrowser=True,
        css=_GRADIO_CSS,
        theme=_GRADIO_THEME,
    )


if __name__ == "__main__":
    main()
