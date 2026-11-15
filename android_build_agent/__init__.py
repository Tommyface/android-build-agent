"""android_build_agent — 把 Android 构建能力暴露给 AI Agent 调用。

对外两种入口：
  · MCP 服务（stdio）：供 Claude Desktop / Cursor / Cline 等 MCP 客户端调用
  · CLI（--json）：供只能跑 shell 的 Agent 调用

设计约束：
  · 只接受 profiles.json 里登记过的项目，不允许传任意命令
  · 构建任务限定在白名单里（assemble*/bundle*/lint/clean）
  · 所有工具链、缓存、构建产物都落在 root 下，不污染系统盘
"""

__version__ = "1.0.0"
