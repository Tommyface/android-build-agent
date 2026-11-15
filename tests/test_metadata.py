#!/usr/bin/env python3
"""包元数据一致性校验。

守两类事故（1.0.2 就栽在第一条）：
  1. `android_build_agent/__init__.py` 被写成空文件 / 缺 `__version__`
  2. `pyproject.toml` 的 version 与 `__version__` 不一致

不依赖 pytest，CI 与本地都能跑：
    python tests/test_metadata.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "android_build_agent" / "__init__.py"
PYPROJECT = ROOT / "pyproject.toml"
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" · {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


def read_pyproject_version() -> str | None:
    raw = PYPROJECT.read_text(encoding="utf-8")
    try:
        import tomllib  # Python 3.11+

        return tomllib.loads(raw)["project"]["version"]
    except ModuleNotFoundError:
        # 3.9 / 3.10：退化为正则，[project] 之前没有 version 字段
        m = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"', raw)
        return m.group(1) if m else None


def main() -> int:
    check("pyproject.toml 存在", PYPROJECT.exists(), str(PYPROJECT))
    check("__init__.py 存在", INIT.exists(), str(INIT))
    if FAILED:
        return 1

    init_text = INIT.read_text(encoding="utf-8")
    size = INIT.stat().st_size
    check("__init__.py 非空", size > 0, f"{size} 字节（1.0.2 事故就是这里被写成 0 字节）")

    m = re.search(r'(?m)^__version__\s*=\s*"([^"]+)"', init_text)
    check("__init__.py 定义了 __version__", m is not None, init_text[:120])
    project_version = read_pyproject_version()
    check("pyproject.toml 能读到 version", bool(project_version), str(project_version))

    if m and project_version:
        check(
            "两处版本号一致",
            m.group(1) == project_version,
            f"__init__.py={m.group(1)}  pyproject={project_version}",
        )
        check(
            "版本号形如 x.y.z",
            bool(re.fullmatch(r"\d+\.\d+\.\d+", project_version)),
            project_version,
        )

    # 入口点仍然指向存在的模块函数，避免重命名后 console_script 失效
    raw = PYPROJECT.read_text(encoding="utf-8")
    scripts = re.findall(r'(?m)^([\w-]+)\s*=\s*"([\w.]+):(\w+)"\s*$', raw)
    check("至少有一个 console_scripts 入口", len(scripts) >= 1, str(scripts))
    for name, module, func in scripts:
        mod_path = ROOT / (module.replace(".", "/") + ".py")
        ok = mod_path.exists() and (f"def {func}(" in mod_path.read_text(encoding="utf-8"))
        check(f"入口 {name} → {module}:{func}", ok, str(mod_path))

    if FAILED:
        print("\n元数据校验失败：" + ", ".join(FAILED), file=sys.stderr)
        return 1
    print("\n元数据校验通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
