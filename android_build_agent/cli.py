"""命令行入口：给只会跑 shell 的 Agent 用。

约定：加 --json 时，stdout 只输出一行 JSON，日志走 stderr。
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from . import __version__
from .builder import build, write_manifest
from .config import ConfigError, load_profiles, profile_exists_hint, resolve, validate_project_name
from .toolchain import ensure_all


def _logger(json_mode: bool):
    if json_mode:
        return lambda msg: print(msg, file=sys.stderr, flush=True)
    return lambda msg: print(msg, flush=True)


def _emit(payload: dict, json_mode: bool) -> int:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok", True) else 1


def cmd_list(args) -> int:
    data = load_profiles(args.profiles)
    items = []
    for name, entry in (data.get("projects") or {}).items():
        try:
            profile = resolve(name, data)
        except ConfigError:
            continue
        items.append(
            {
                "name": name,
                "repo": entry.get("repo", ""),
                "branch": entry.get("branch") or "main",
                "task": profile.task,
                "sdkPlatforms": profile.sdk_platforms,
                "gradleVersion": profile.gradle_version,
                "root": str(profile.root),
                "projectDir": str(profile.project_dir),
                "toolchainReady": (profile.jdk_dir / "bin").exists() and profile.gradle_dir.exists(),
                "sourceReady": profile.project_dir.exists(),
            }
        )
    return _emit(
        {
            "ok": True,
            "configPath": data.get("_path"),
            "configExists": data.get("_exists"),
            "count": len(items),
            "profiles": items,
            "hint": profile_exists_hint(data),
        },
        args.json,
    )


def cmd_install(args) -> int:
    data = load_profiles(args.profiles)
    name = validate_project_name(args.profile)
    profile = resolve(
        name,
        data,
        root=args.root,
        repo=args.repo,
        branch=args.branch,
        sdk_platforms=args.sdk,
        build_tools=args.build_tools,
        gradle_version=args.gradle,
        task=args.task,
    )
    log = _logger(args.json)
    log(f"项目：{profile.name}  根目录：{profile.root}")
    paths = ensure_all(profile, log)
    payload = {"ok": True, "profile": profile.name, "root": str(profile.root), "paths": paths}
    if args.build:
        result = build(profile, log, clean=False, timeout=args.timeout)
        write_manifest(profile, result)
        payload["build"] = result
        payload["ok"] = bool(result.get("ok"))
    return _emit(payload, args.json)


def cmd_build(args) -> int:
    data = load_profiles(args.profiles)
    name = validate_project_name(args.profile)
    profile = resolve(name, data, root=args.root, task=args.task, gradle_version=args.gradle)
    log = _logger(args.json)

    if not (profile.jdk_dir / "bin").exists():
        log("工具链不完整，先执行安装")
        if not args.no_install:
            ensure_all(profile, log)

    result = build(
        profile,
        log,
        task=args.task,
        clean=args.clean,
        timeout=args.timeout,
        update_source=args.update,
    )
    manifest = write_manifest(profile, result)
    result["manifestPath"] = str(manifest)
    return _emit(result, args.json)


def cmd_artifacts(args) -> int:
    data = load_profiles(args.profiles)
    name = validate_project_name(args.profile)
    profile = resolve(name, data, root=args.root)
    manifest = profile.out_dir / f"{profile.name}-last-build.json"
    if not manifest.exists():
        return _emit({"ok": False, "profile": name, "error": "还没有构建记录"}, args.json)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["manifestPath"] = str(manifest)
    return _emit(payload, args.json)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="android-build-agent",
        description="给 AI Agent 调用的 Android 构建工具（MCP / CLI 双入口）",
    )
    parser.add_argument("--version", action="version", version=f"android-build-agent {__version__}")
    parser.add_argument("--profiles", help="profiles.json 路径（默认找当前目录或包目录）")
    parser.add_argument("--json", action="store_true", help="输出单行 JSON，便于 Agent 解析")
    # 子命令也接受 --json / --profiles，放在命令前后都行
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--profiles", dest="profiles", default=argparse.SUPPRESS,
                        help="profiles.json 路径")
    common.add_argument("--json", dest="json", action="store_true", default=argparse.SUPPRESS,
                        help="输出单行 JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", parents=[common], help="列出已登记的项目")
    p_list.set_defaults(func=cmd_list)

    p_install = sub.add_parser("install", parents=[common], help="安装工具链（可选顺便构建）")
    p_install.add_argument("profile")
    p_install.add_argument("--root")
    p_install.add_argument("--repo")
    p_install.add_argument("--branch")
    p_install.add_argument("--sdk", nargs="*", help="platforms，如 android-34")
    p_install.add_argument("--build-tools", nargs="*", help="如 34.0.0")
    p_install.add_argument("--gradle", help="Gradle 版本，如 8.7")
    p_install.add_argument("--task")
    p_install.add_argument("--timeout", type=int, default=7200)
    p_install.add_argument("--build", action="store_true", help="装完立刻构建一次")
    p_install.set_defaults(func=cmd_install)

    p_build = sub.add_parser("build", parents=[common], help="构建（增量）")
    p_build.add_argument("profile")
    p_build.add_argument("--root")
    p_build.add_argument("--task", help="如 assembleDebug / assembleRelease / :app:assembleDebug")
    p_build.add_argument("--gradle")
    p_build.add_argument("--clean", action="store_true")
    p_build.add_argument("--update", action="store_true", help="先 git pull")
    p_build.add_argument("--no-install", action="store_true", help="缺工具链时不自动安装")
    p_build.add_argument("--timeout", type=int, default=3600)
    p_build.set_defaults(func=cmd_build)

    p_art = sub.add_parser("artifacts", parents=[common], help="读取最近一次构建结果与 APK 清单")
    p_art.add_argument("profile")
    p_art.add_argument("--root")
    p_art.set_defaults(func=cmd_artifacts)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        return _emit({"ok": False, "error": str(exc), "type": "config"}, args.json)
    except Exception as exc:  # noqa: BLE001
        return _emit(
            {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=3),
            },
            args.json,
        )


if __name__ == "__main__":
    raise SystemExit(main())
