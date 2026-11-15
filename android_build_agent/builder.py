"""源码获取与构建执行。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Callable

from .config import ConfigError, Profile, check_task
from .toolchain import build_env

Log = Callable[[str], None]

# Gradle 版本低于该值时只提示，不阻断
AGP_MIN_GRADLE = {
    "8.7": "8.7",
    "8.6": "8.7",
    "8.5": "8.7",
    "8.4": "8.6",
    "8.3": "8.4",
    "8.2": "8.2",
    "8.1": "8.0",
    "8.0": "8.0",
}


def github_zip_url(repo: str, branch: str) -> str | None:
    m = re.match(r"^https?://github\.com/([^/]+)/([^/.]+?)(\.git)?/?$", repo.strip())
    if not m:
        return None
    return f"https://codeload.github.com/{m.group(1)}/{m.group(2)}/zip/refs/heads/{branch}"


def fetch_source(profile: Profile, log: Log, *, update: bool = False) -> Path:
    target = profile.project_dir
    if (target / "settings.gradle").exists() or (target / "build.gradle").exists():
        if update and shutil.which("git"):
            log("更新源码：git pull")
            subprocess.run(["git", "pull", "--ff-only"], cwd=target, capture_output=True, text=True)
        else:
            log(f"源码已存在：{target}")
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("git"):
        log(f"git clone {profile.repo}（branch={profile.branch}）")
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", profile.branch, profile.repo, str(target)],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if proc.returncode == 0:
            return target
        log("clone 失败，改用 zip 下载：" + (proc.stderr or "")[-300:])

    zip_url = github_zip_url(profile.repo, profile.branch)
    if not zip_url:
        raise ConfigError(f"无法获取源码：{profile.repo}（非 GitHub 且 git 不可用）")

    from .toolchain import download, extract

    archive = download([zip_url], profile.downloads_dir / f"{profile.name}-src.zip", log)
    extract(archive, target, log)
    log(f"源码就绪：{target}")
    return target


def _merge_gradle_properties(path: Path, profile: Profile) -> None:
    """只改必要的几项，保留项目原有配置。"""
    wanted = {
        "org.gradle.jvmargs": (
            "-Xmx1536m -XX:MaxMetaspaceSize=384m -Dfile.encoding=UTF-8 "
            f"-Djava.io.tmpdir={str(profile.tmp_dir).replace(os.sep, '/')}"
        ),
        "kotlin.compiler.execution.strategy": "in-process",
        "org.gradle.parallel": "false",
        "org.gradle.workers.max": "2",
        "android.useAndroidX": "true",
    }
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for key, value in wanted.items():
        pattern = re.compile(r"^\s*" + re.escape(key) + r"\s*=")
        replaced = False
        for i, line in enumerate(lines):
            if pattern.match(line):
                lines[i] = f"{key}={value}"
                replaced = True
                break
        if not replaced:
            lines.append(f"{key}={value}")
    path.write_text("\r\n".join(lines) + "\r\n" if os.name == "nt" else "\n".join(lines) + "\n", encoding="utf-8")


def _write_local_properties(project: Path, profile: Profile) -> None:
    sdk = str(profile.sdk_dir)
    if os.name == "nt":
        sdk = sdk.replace("\\", "\\\\")
    (project / "local.properties").write_text(f"sdk.dir={sdk}\n", encoding="utf-8")


def detect_agp(project: Path) -> str | None:
    for rel in ("build.gradle", "build.gradle.kts", "app/build.gradle", "app/build.gradle.kts", "gradle/libs.versions.toml"):
        f = project / rel
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"com\.android\.tools\.build:gradle:([0-9.]+)", text)
        if m:
            return m.group(1)
        m = re.search(r"agp\s*=\s*\"([0-9.]+)\"", text)
        if m:
            return m.group(1)
    return None


def agp_hint(agp: str | None, gradle_version: str) -> str | None:
    if not agp:
        return None
    major_minor = ".".join(agp.split(".")[:2])
    need = AGP_MIN_GRADLE.get(major_minor)
    if not need:
        return None
    try:
        if [int(x) for x in gradle_version.split(".")[:2]] < [int(x) for x in need.split(".")[:2]]:
            return f"AGP {agp} 建议 Gradle ≥ {need}，当前 {gradle_version}，可能构建失败"
    except ValueError:
        return None
    return None


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 256), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_apks(project: Path, profile: Profile, log: Log) -> list[dict]:
    results: list[dict] = []
    apk_root = project / "app" / "build" / "outputs" / "apk"
    if not apk_root.exists():
        # 多模块项目：兜底扫整个项目
        apk_root = project / "build" / "outputs" / "apk"
    if not apk_root.exists():
        return results
    profile.out_dir.mkdir(parents=True, exist_ok=True)
    for apk in apk_root.rglob("*.apk"):
        dest = profile.out_dir / f"{profile.name}-{apk.name}"
        shutil.copy2(apk, dest)
        results.append(
            {
                "path": str(dest),
                "source": str(apk),
                "size": dest.stat().st_size,
                "sha256": sha256_of(dest),
                "variant": apk.parent.name,
            }
        )
        log(f"产物：{dest}（{dest.stat().st_size // 1024} KB）")
    return results


def build(
    profile: Profile,
    log: Log = print,
    *,
    task: str | None = None,
    clean: bool = False,
    timeout: int = 3600,
    update_source: bool = False,
) -> dict:
    task = check_task(task or profile.task)
    started = time.time()
    project = fetch_source(profile, log, update=update_source)

    _write_local_properties(project, profile)
    _merge_gradle_properties(project / "gradle.properties", profile)
    log("已写入 local.properties 与受控的 gradle.properties")

    agp = detect_agp(project)
    if agp:
        log(f"检测到 AGP {agp}")
        hint = agp_hint(agp, profile.gradle_version)
        if hint:
            log("警告：" + hint)

    env = build_env(profile)
    tasks = (["clean"] if clean else []) + [task]
    gradle_bin = profile.gradle_dir / "bin" / ("gradle.bat" if os.name == "nt" else "gradle")
    if not gradle_bin.exists():
        raise RuntimeError(f"Gradle 不存在，请先安装工具链：{gradle_bin}")

    cmd = [str(gradle_bin), *tasks, "--no-daemon", "--console=plain"]
    log("执行：" + " ".join(cmd))
    proc = subprocess.run(cmd, cwd=project, env=env, capture_output=True, text=True, timeout=timeout)
    log_tail = "\n".join((proc.stdout or "").splitlines()[-60:])
    apks = collect_apks(project, profile, log) if proc.returncode == 0 else []

    result = {
        "ok": proc.returncode == 0,
        "exitCode": proc.returncode,
        "profile": profile.name,
        "task": task,
        "clean": clean,
        "projectDir": str(project),
        "durationSec": round(time.time() - started, 1),
        "apks": apks,
        "logTail": log_tail,
        "agp": agp,
        "hint": agp_hint(agp, profile.gradle_version),
    }
    if proc.returncode != 0:
        result["errorTail"] = "\n".join((proc.stderr or "").splitlines()[-30:])
    return result


def write_manifest(profile: Profile, result: dict) -> Path:
    """把最近一次构建结果落盘，便于别的 Agent 读取。"""
    out = profile.out_dir
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{profile.name}-last-build.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
