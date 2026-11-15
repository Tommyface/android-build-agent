"""MCP 服务（stdio）：把构建能力暴露给 Claude Desktop / Cursor / Cline 等客户端。

实现要点：
  · stdio 传输，按行分隔的 JSON-RPC 2.0
  · 只暴露 4 个工具，参数全部走白名单校验，不接受任意命令
  · 所有返回都是 JSON 文本，便于模型直接理解
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from . import __version__
from .builder import build, write_manifest
from .config import ConfigError, load_profiles, resolve, validate_project_name
from .toolchain import ensure_all

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "android-build-agent"


def log(msg: str) -> None:
    print(f"[{SERVER_NAME}] {msg}", file=sys.stderr, flush=True)


TOOLS: list[dict[str, Any]] = [
    {
        "name": "android_list_profiles",
        "description": "列出已登记的 Android 项目（profile）、对应仓库、SDK 版本与工具链是否就绪。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "android_install_toolchain",
        "description": (
            "为指定项目安装 JDK17 / Gradle / Android SDK（全部落在 root 目录，幂等可重跑）。"
            "可选顺便拉源码并构建一次。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "profile": {"type": "string", "description": "profiles.json 里的项目名"},
                "root": {"type": "string", "description": "安装根目录，默认按平台选择"},
                "sdkPlatforms": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "如 ['android-34']",
                },
                "buildTools": {"type": "array", "items": {"type": "string"}, "description": "如 ['34.0.0']"},
                "gradleVersion": {"type": "string", "description": "如 8.7；默认取 profile 配置"},
                "buildAfter": {"type": "boolean", "description": "装完是否立刻构建一次"},
            },
            "required": ["profile"],
            "additionalProperties": False,
        },
    },
    {
        "name": "android_build",
        "description": (
            "构建指定项目。返回 ok / exitCode / 耗时 / APK 列表（路径、大小、sha256）/ 日志尾部。"
            "任务限定在 assembleDebug、assembleRelease、bundleRelease、lint 等白名单内。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "profile": {"type": "string"},
                "task": {"type": "string", "description": "默认 assembleDebug；支持 :app:assembleDebug"},
                "clean": {"type": "boolean", "description": "是否先 clean"},
                "updateSource": {"type": "boolean", "description": "构建前 git pull"},
                "timeoutSec": {"type": "integer", "description": "超时秒数，默认 3600"},
            },
            "required": ["profile"],
            "additionalProperties": False,
        },
    },
    {
        "name": "android_get_build_result",
        "description": "读取某个项目最近一次构建结果（含 APK 清单与日志尾部），不触发构建。",
        "inputSchema": {
            "type": "object",
            "properties": {"profile": {"type": "string"}},
            "required": ["profile"],
            "additionalProperties": False,
        },
    },
]


def _text(payload: Any, *, is_error: bool = False) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "isError": is_error,
    }


def _profiles_data() -> dict:
    return load_profiles()


def tool_list_profiles(args: dict) -> dict:
    data = _profiles_data()
    items = []
    for name in (data.get("projects") or {}):
        try:
            profile = resolve(name, data)
        except ConfigError:
            continue
        items.append(
            {
                "name": name,
                "repo": profile.repo,
                "branch": profile.branch,
                "task": profile.task,
                "sdkPlatforms": profile.sdk_platforms,
                "gradleVersion": profile.gradle_version,
                "root": str(profile.root),
                "toolchainReady": (profile.jdk_dir / "bin").exists() and profile.gradle_dir.exists(),
                "sourceReady": profile.project_dir.exists(),
            }
        )
    return _text({"ok": True, "count": len(items), "profiles": items, "configPath": data.get("_path")})


def tool_install(args: dict) -> dict:
    data = _profiles_data()
    name = validate_project_name(str(args.get("profile", "")))
    profile = resolve(
        name,
        data,
        root=args.get("root"),
        sdk_platforms=args.get("sdkPlatforms"),
        build_tools=args.get("buildTools"),
        gradle_version=args.get("gradleVersion"),
    )
    log(f"安装工具链：{profile.name} → {profile.root}")
    paths = ensure_all(profile, log)
    payload: dict[str, Any] = {"ok": True, "profile": profile.name, "paths": paths}
    if args.get("buildAfter"):
        result = build(profile, log)
        write_manifest(profile, result)
        payload["build"] = result
        payload["ok"] = bool(result.get("ok"))
    return _text(payload, is_error=not payload["ok"])


def tool_build(args: dict) -> dict:
    data = _profiles_data()
    name = validate_project_name(str(args.get("profile", "")))
    profile = resolve(name, data, task=args.get("task"))
    if not (profile.jdk_dir / "bin").exists():
        log("工具链缺失，自动补齐")
        ensure_all(profile, log)
    result = build(
        profile,
        log,
        task=args.get("task"),
        clean=bool(args.get("clean")),
        timeout=int(args.get("timeoutSec") or 3600),
        update_source=bool(args.get("updateSource")),
    )
    manifest = write_manifest(profile, result)
    result["manifestPath"] = str(manifest)
    return _text(result, is_error=not result.get("ok"))


def tool_get_result(args: dict) -> dict:
    data = _profiles_data()
    name = validate_project_name(str(args.get("profile", "")))
    profile = resolve(name, data)
    manifest = profile.out_dir / f"{profile.name}-last-build.json"
    if not manifest.exists():
        return _text({"ok": False, "error": "还没有构建记录", "profile": name}, is_error=True)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["manifestPath"] = str(manifest)
    return _text(payload)


HANDLERS = {
    "android_list_profiles": tool_list_profiles,
    "android_install_toolchain": tool_install,
    "android_build": tool_build,
    "android_get_build_result": tool_get_result,
}


def send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle(msg: dict) -> dict | None:
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
            },
        }
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = HANDLERS.get(name)
        if not handler:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"未知工具：{name}"},
            }
        try:
            return {"jsonrpc": "2.0", "id": msg_id, "result": handler(args)}
        except ConfigError as exc:
            return {"jsonrpc": "2.0", "id": msg_id, "result": _text({"ok": False, "error": str(exc)}, is_error=True)}
        except Exception as exc:  # noqa: BLE001
            log("工具执行异常：" + traceback.format_exc(limit=3))
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": _text({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, is_error=True),
            }
    if msg_id is None:
        return None
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"未实现的方法：{method}"}}


def main() -> int:
    log(f"启动 {SERVER_NAME} {__version__}（stdio）")
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            log("收到非 JSON 行，已忽略")
            continue
        reply = handle(msg)
        if reply is not None:
            send(reply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
