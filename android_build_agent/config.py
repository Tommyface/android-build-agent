"""配置解析：profiles.json + 命令行覆盖 + 任务白名单。"""

from __future__ import annotations

import json
import os
import string
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 允许构建的任务后缀（可以带模块前缀，如 :app:assembleDebug）
TASK_SUFFIXES = (
    "assembleDebug",
    "assembleRelease",
    "assembleAndroidTest",
    "bundleDebug",
    "bundleRelease",
    "lint",
    "lintDebug",
    "compileDebugKotlin",
)

DEFAULT_PROFILES = {
    "defaults": {
        "sdkPlatforms": ["android-33"],
        "buildTools": ["33.0.2"],
        "gradleVersion": "7.6.3",
        "task": "assembleDebug",
    },
    "projects": {},
}


class ConfigError(RuntimeError):
    """配置或参数不合法。"""


@dataclass
class Profile:
    name: str
    repo: str
    branch: str = "main"
    sdk_platforms: list[str] = field(default_factory=lambda: ["android-33"])
    build_tools: list[str] = field(default_factory=lambda: ["33.0.2"])
    extra_packages: list[str] = field(default_factory=list)
    gradle_version: str = "7.6.3"
    task: str = "assembleDebug"
    root: Path = field(default_factory=lambda: Path(default_root()))

    # ---- 派生路径 ----
    @property
    def toolchain_dir(self) -> Path:
        return self.root / "toolchain"

    @property
    def jdk_dir(self) -> Path:
        return self.toolchain_dir / "jdk-17"

    @property
    def gradle_dir(self) -> Path:
        return self.toolchain_dir / f"gradle-{self.gradle_version}"

    @property
    def sdk_dir(self) -> Path:
        return self.toolchain_dir / "sdk"

    @property
    def gradle_home(self) -> Path:
        return self.root / "gradle-home"

    @property
    def tmp_dir(self) -> Path:
        return self.root / "tmp"

    @property
    def out_dir(self) -> Path:
        return self.root / "out"

    @property
    def downloads_dir(self) -> Path:
        return self.root / "downloads"

    @property
    def project_dir(self) -> Path:
        return self.root / "projects" / self.name

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "repo": self.repo,
            "branch": self.branch,
            "sdkPlatforms": self.sdk_platforms,
            "buildTools": self.build_tools,
            "extraPackages": self.extra_packages,
            "gradleVersion": self.gradle_version,
            "task": self.task,
            "root": str(self.root),
            "projectDir": str(self.project_dir),
        }


def default_root() -> str:
    """默认根目录：Windows 上优先用存在的 E 盘，其他系统用用户目录。"""
    if os.name == "nt":
        for drive in ("E", "D", "F"):
            if Path(f"{drive}:\\").exists():
                return f"{drive}:\\android-build"
        return str(Path.home() / "android-build")
    return str(Path.home() / "android-build")


def profiles_path(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("ANDROID_BUILD_PROFILES")
    if env:
        return Path(env).expanduser().resolve()
    # 1) 当前工作目录  2) 包所在目录的上一级
    for candidate in (Path.cwd() / "profiles.json", Path(__file__).resolve().parent.parent / "profiles.json"):
        if candidate.exists():
            return candidate
    return Path.cwd() / "profiles.json"


def load_profiles(explicit: str | None = None) -> dict:
    path = profiles_path(explicit)
    if not path.exists():
        data = json.loads(json.dumps(DEFAULT_PROFILES))
        data["_path"] = str(path)
        data["_exists"] = False
        return data
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"profiles.json 不是合法 JSON：{path} · {exc}") from exc
    data.setdefault("defaults", DEFAULT_PROFILES["defaults"])
    data.setdefault("projects", {})
    data["_path"] = str(path)
    data["_exists"] = True
    return data


def _as_list(value, fallback):
    if value is None:
        return list(fallback)
    if isinstance(value, str):
        return [value]
    return list(value)


def resolve(
    name: str,
    data: dict,
    *,
    root: str | None = None,
    repo: str | None = None,
    branch: str | None = None,
    sdk_platforms=None,
    build_tools=None,
    gradle_version: str | None = None,
    task: str | None = None,
) -> Profile:
    defaults = data.get("defaults") or {}
    projects = data.get("projects") or {}
    entry = projects.get(name)

    resolved_repo = repo or (entry or {}).get("repo")
    if not resolved_repo:
        known = ", ".join(sorted(projects.keys())) or "（空）"
        raise ConfigError(f"未找到项目 '{name}' 的 repo；已登记的项目：{known}")

    profile = Profile(
        name=name,
        repo=resolved_repo,
        branch=branch or (entry or {}).get("branch") or "main",
        sdk_platforms=_as_list(
            sdk_platforms or (entry or {}).get("sdkPlatforms") or defaults.get("sdkPlatforms"),
            ["android-33"],
        ),
        build_tools=_as_list(
            build_tools or (entry or {}).get("buildTools") or defaults.get("buildTools"),
            ["33.0.2"],
        ),
        extra_packages=_as_list((entry or {}).get("extraSdkPackages"), []),
        gradle_version=gradle_version
        or (entry or {}).get("gradleVersion")
        or defaults.get("gradleVersion")
        or "7.6.3",
        task=task or (entry or {}).get("task") or defaults.get("task") or "assembleDebug",
        root=Path(root or (entry or {}).get("root") or default_root()).expanduser().resolve(),
    )
    profile.task = check_task(profile.task)
    return profile


def check_task(task: str) -> str:
    """只允许白名单任务；支持 :module:task 形式。"""
    cleaned = task.strip()
    if not cleaned:
        raise ConfigError("task 不能为空")
    if any(ch in cleaned for ch in (";", "&", "|", "$", "`", "\n", "\r")):
        raise ConfigError(f"task 含有非法字符：{task!r}")
    suffix = cleaned.split(":")[-1]
    if suffix not in TASK_SUFFIXES and suffix != "clean":
        raise ConfigError(
            "task 不在白名单内：" + task + "；可用：" + ", ".join(TASK_SUFFIXES) + ", clean"
        )
    return cleaned


def profile_exists_hint(data: dict) -> str:
    """没有配置时给 Agent 一句可执行提示。"""
    if data.get("_exists"):
        return ""
    return (
        "未找到 profiles.json，当前只带内置默认值。请在 "
        + str(data.get("_path"))
        + " 创建配置，或在调用时直接传 repo / branch / sdkPlatforms。"
    )


def validate_project_name(name: str) -> str:
    allowed = set(string.ascii_letters + string.digits + "._-")
    if not name or any(ch not in allowed for ch in name):
        raise ConfigError(f"项目名只能是字母/数字/._-：{name!r}")
    return name


if __name__ == "__main__":  # 便于快速自检：python -m android_build_agent.config
    info = load_profiles()
    print(json.dumps({k: v for k, v in info.items() if not k.startswith("_")}, ensure_ascii=False, indent=2))
    sys.exit(0)
