#!/usr/bin/env python3
"""MCP 冒烟测试：起一个真实 stdio 进程，走完握手 → 工具列表 → 列表工具 → 安全拦截。

不依赖 pytest，CI 与本地都能直接跑：
    python tests/smoke_mcp.py
失败时退出码非 0，并在 stderr 打印具体断言。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" · {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


class McpClient:
    def __init__(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "android_build_agent.mcp_server"],
            cwd=str(ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        self._id = 0

    def send(self, method: str, params: dict | None = None, *, notify: bool = False) -> None:
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self._id += 1
            msg["id"] = self._id
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def recv(self) -> dict:
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        if not line.strip():
            raise RuntimeError("服务没有返回任何内容（可能启动失败）")
        return json.loads(line)

    def call(self, name: str, arguments: dict) -> dict:
        self.send("tools/call", {"name": name, "arguments": arguments})
        reply = self.recv()
        text = reply["result"]["content"][0]["text"]
        return {"isError": reply["result"].get("isError", False), "payload": json.loads(text)}

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def main() -> int:
    client = McpClient()
    try:
        client.send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "smoke-test", "version": "0"},
        })
        init = client.recv()["result"]
        check("initialize 返回协议版本", init["protocolVersion"] == "2024-11-05", str(init))
        check("serverInfo 名称正确", init["serverInfo"]["name"] == "android-build-agent", str(init["serverInfo"]))
        check("声明 tools 能力", "tools" in init.get("capabilities", {}), str(init.get("capabilities")))

        client.send("notifications/initialized", notify=True)

        client.send("tools/list")
        tools = client.recv()["result"]["tools"]
        names = {t["name"] for t in tools}
        expect = {
            "android_list_profiles",
            "android_install_toolchain",
            "android_build",
            "android_get_build_result",
        }
        check("工具列表包含 4 个工具", names == expect, str(names))
        check("每个工具都有 inputSchema", all("inputSchema" in t for t in tools))

        listed = client.call("android_list_profiles", {})
        check("list_profiles 调用成功", listed["payload"].get("ok") is True, str(listed["payload"])[:200])
        check("返回 profile 列表", isinstance(listed["payload"].get("profiles"), list))

        unknown = client.call("android_build", {"profile": "profile_not_exist_xyz"})
        check("未知 profile 被拒绝", unknown["isError"] and "未找到项目" in unknown["payload"].get("error", ""),
              str(unknown["payload"])[:200])

        injected = client.call("android_build", {"profile": "shiye", "task": "assembleDebug && whoami"})
        check("注入型 task 被拒绝", injected["isError"] and "非法字符" in injected["payload"].get("error", ""),
              str(injected["payload"])[:200])

        # 未知工具应返回 JSON-RPC error，而不是崩溃
        client.send("tools/call", {"name": "android_nope", "arguments": {}})
        err = client.recv()
        check("未知工具返回 error 而不是崩溃",
              err.get("error", {}).get("code") == -32601, str(err)[:200])
    finally:
        client.close()

    if FAILED:
        print("\n冒烟测试失败：" + ", ".join(FAILED), file=sys.stderr)
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
