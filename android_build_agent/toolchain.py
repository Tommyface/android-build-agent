"""工具链管理：JDK / Gradle / Android SDK 的下载、解压、安装。

原则：
  · 全部落在 profile.root 下，不写系统目录、不需要 root/管理员
  · 已存在就跳过（幂等），下载失败按镜像顺序重试
  · 只用标准库，Windows / macOS / Linux 通用
"""

from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from .config import Profile

Log = Callable[[str], None]

JDK_VERSION = "17.0.21_9"
CMDTOOLS_VERSION = "11076708"

UA = "android-build-agent/1.0 (+https://github.com/Tommyface)"


def _os_tag() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


def _jdk_urls() -> list[str]:
    tag = _os_tag()
    if tag == "windows":
        file = f"OpenJDK17U-jdk_x64_windows_hotspot_{JDK_VERSION}.zip"
    elif tag == "mac":
        file = f"OpenJDK17U-jdk_x64_mac_hotspot_{JDK_VERSION}.tar.gz"
    else:
        file = f"OpenJDK17U-jdk_x64_linux_hotspot_{JDK_VERSION}.tar.gz"
    tsinghua = f"https://mirrors.tuna.tsinghua.edu.cn/Adoptium/17/jdk/x64/{tag}/{file}"
    tencent = f"https://mirrors.cloud.tencent.com/Adoptium/17/jdk/x64/{tag}/{file}"
    github = (
        "https://github.com/adoptium/temurin17-binaries/releases/download/"
        f"jdk-{JDK_VERSION.replace('_', '%2B')}/{file}"
    )
    return [tsinghua, tencent, github]


def _gradle_urls(version: str) -> list[str]:
    return [
        f"https://mirrors.cloud.tencent.com/gradle/gradle-{version}-bin.zip",
        f"https://services.gradle.org/distributions/gradle-{version}-bin.zip",
    ]


def _cmdtools_urls() -> list[str]:
    tag = _os_tag()
    file = f"commandlinetools-{tag}-{CMDTOOLS_VERSION}_latest.zip"
    return [
        f"https://dl.google.com/android/repository/{file}",
        f"https://mirrors.cloud.tencent.com/AndroidSDK/{file}",
    ]


def download(urls: list[str], dest: Path, log: Log) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        log(f"已存在，跳过下载：{dest.name}")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    for url in urls:
        try:
            log(f"下载 {dest.name} ← {url}")
            tmp = dest.with_suffix(dest.suffix + ".part")
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=1800) as resp, open(tmp, "wb") as fh:
                shutil.copyfileobj(resp, fh, length=1024 * 512)
            tmp.replace(dest)
            log(f"下载完成：{dest.name}（{dest.stat().st_size // 1024 // 1024} MB）")
            return dest
        except Exception as exc:  # noqa: BLE001 - 逐个镜像容错
            last_error = exc
            log(f"下载失败，换下一个源：{url} · {exc}")
    raise RuntimeError(f"{dest.name} 所有下载源都失败：{last_error}")


def extract(archive: Path, target: Path, log: Log) -> Path:
    """解压并返回唯一顶层目录。"""
    work = target.parent / (target.name + "-x")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    log(f"解压 {archive.name} → {work}")
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(work)
    else:
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(work)
    entries = [p for p in work.iterdir() if p.is_dir()]
    if len(entries) != 1:
        raise RuntimeError(f"{archive.name} 解压后顶层目录不唯一：{[p.name for p in entries]}")
    if target.exists():
        shutil.rmtree(target)
    entries[0].rename(target)
    shutil.rmtree(work, ignore_errors=True)
    return target


def _is_windows() -> bool:
    return os.name == "nt"


def ensure_jdk(profile: Profile, log: Log) -> Path:
    java = profile.jdk_dir / "bin" / ("java.exe" if _is_windows() else "java")
    if java.exists():
        log(f"JDK 已就绪：{profile.jdk_dir}")
        return profile.jdk_dir
    archive = download(_jdk_urls(), profile.downloads_dir / "jdk17.archive", log)
    extract(archive, profile.jdk_dir, log)
    if not java.exists():
        raise RuntimeError(f"JDK 解压后找不到 java：{java}")
    log(f"JDK 安装完成：{profile.jdk_dir}")
    return profile.jdk_dir


def ensure_gradle(profile: Profile, log: Log) -> Path:
    gradle_bin = profile.gradle_dir / "bin" / ("gradle.bat" if _is_windows() else "gradle")
    if gradle_bin.exists():
        log(f"Gradle {profile.gradle_version} 已就绪")
        return profile.gradle_dir
    archive = download(
        _gradle_urls(profile.gradle_version),
        profile.downloads_dir / f"gradle-{profile.gradle_version}-bin.zip",
        log,
    )
    extract(archive, profile.gradle_dir, log)
    log(f"Gradle 安装完成：{profile.gradle_dir}")
    return profile.gradle_dir


def _sdkmanager(profile: Profile) -> Path:
    name = "sdkmanager.bat" if _is_windows() else "sdkmanager"
    return profile.sdk_dir / "cmdline-tools" / "latest" / "bin" / name


def ensure_sdk(profile: Profile, log: Log) -> Path:
    manager = _sdkmanager(profile)
    if not manager.exists():
        archive = download(_cmdtools_urls(), profile.downloads_dir / "cmdtools.zip", log)
        work = extract(archive, profile.sdk_dir / "cmdline-tools" / "latest", log)
        if work != profile.sdk_dir / "cmdline-tools" / "latest":
            raise RuntimeError("cmdline-tools 解压位置异常")
        if not _is_windows():
            manager.chmod(manager.stat().st_mode | stat.S_IEXEC)

    env = build_env(profile)
    packages = [f"platforms;{p}" for p in profile.sdk_platforms]
    packages += [f"build-tools;{b}" for b in profile.build_tools]
    packages += profile.extra_packages
    if packages:
        log("安装 SDK 组件：" + ", ".join(packages))
        proc = subprocess.run(
            [str(manager), f"--sdk_root={profile.sdk_dir}", *packages],
            env=env,
            capture_output=True,
            text=True,
            timeout=3600,
        )
        if proc.returncode != 0:
            log("sdkmanager 提示：" + (proc.stdout or proc.stderr or "")[-800:])

    # 接受许可（Gradle 需要）
    try:
        subprocess.run(
            [str(manager), f"--sdk_root={profile.sdk_dir}", "--licenses"],
            env=env,
            input="y\n" * 60,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except Exception as exc:  # noqa: BLE001
        log(f"接受许可时出现异常（通常可忽略）：{exc}")

    log(f"SDK 就绪：{profile.sdk_dir}")
    return profile.sdk_dir


def build_env(profile: Profile) -> dict:
    env = dict(os.environ)
    env["JAVA_HOME"] = str(profile.jdk_dir)
    env["GRADLE_USER_HOME"] = str(profile.gradle_home)
    env["ANDROID_HOME"] = str(profile.sdk_dir)
    env["ANDROID_SDK_ROOT"] = str(profile.sdk_dir)
    env["TMP"] = str(profile.tmp_dir)
    env["TEMP"] = str(profile.tmp_dir)
    env["TMPDIR"] = str(profile.tmp_dir)
    path_key = "Path" if _is_windows() and "Path" in env else "PATH"
    env[path_key] = str(profile.jdk_dir / "bin") + os.pathsep + env.get(path_key, "")
    for d in (profile.gradle_home, profile.tmp_dir, profile.out_dir, profile.downloads_dir):
        d.mkdir(parents=True, exist_ok=True)
    return env


def ensure_all(profile: Profile, log: Log = print) -> dict:
    """一次性把 JDK / Gradle / SDK 备齐，返回路径摘要。"""
    for d in (profile.root, profile.downloads_dir, profile.gradle_home, profile.tmp_dir, profile.out_dir):
        d.mkdir(parents=True, exist_ok=True)
    jdk = ensure_jdk(profile, log)
    gradle = ensure_gradle(profile, log)
    sdk = ensure_sdk(profile, log)
    return {
        "jdk": str(jdk),
        "gradle": str(gradle),
        "sdk": str(sdk),
        "gradleHome": str(profile.gradle_home),
        "tmp": str(profile.tmp_dir),
        "out": str(profile.out_dir),
    }
