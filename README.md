---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: df8dd3956ae26494b640a4aee7d3401d_98f865c7e0a311f19919525400638852
    ReservedCode1: 86Pkik2YVSt+j/nqlpAeuQL+NAdIB0/bHycyAJqyR3G8mOjsYz8CeFBsH6jh6dkFK4xDRhmtrbzTe/TqnTk6jD+AN2hGlOeNh+iC3LBTEexqVFe7IietsJyymopw95x4txvhkvpwaJn2kFwJc9wcowBpj3y6i1doENEd1HHhmi+Jo1pBOOMHHpuc9t4=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: df8dd3956ae26494b640a4aee7d3401d_98f865c7e0a311f19919525400638852
    ReservedCode2: 86Pkik2YVSt+j/nqlpAeuQL+NAdIB0/bHycyAJqyR3G8mOjsYz8CeFBsH6jh6dkFK4xDRhmtrbzTe/TqnTk6jD+AN2hGlOeNh+iC3LBTEexqVFe7IietsJyymopw95x4txvhkvpwaJn2kFwJc9wcowBpj3y6i1doENEd1HHhmi+Jo1pBOOMHHpuc9t4=
---

# android-build-agent

把 Android 构建能力做成**可被 AI Agent 调用**的工程：一套 Python 实现，两个入口。

| 入口 | 给谁用 | 形态 |
| --- | --- | --- |
| **MCP 服务** | Claude Desktop / Cursor / Cline / 自研 Agent | stdio + JSON-RPC 2.0，4 个工具 |
| **CLI** | 只能跑 shell 的 Agent | `android-build-agent build shiye --json`，stdout 单行 JSON |

设计原则：**Agent 只能选项目和白名单任务，不能传任意命令**；所有工具链、缓存、产物都落在 root 目录，不碰系统盘。

## 一、目录结构

```
android-build-agent/
├─ profiles.json              # 项目清单（唯一需要你改的文件）
├─ pyproject.toml             # 可 pip install -e .
├─ README.md
└─ android_build_agent/
   ├─ config.py               # profiles 解析 + 任务白名单 + 路径推导
   ├─ toolchain.py            # JDK17 / Gradle / Android SDK 下载安装（跨平台、幂等）
   ├─ builder.py              # 拉源码、写受控 gradle.properties、跑 Gradle、收集 APK + sha256
   ├─ cli.py                  # list / install / build / artifacts
   └─ mcp_server.py           # MCP stdio 服务
```

安装后目录（默认 root）：

```
<root>/
├─ toolchain/   jdk-17、gradle-<版本>、sdk
├─ gradle-home/ 依赖缓存（设 GRADLE_USER_HOME，不落 C 盘）
├─ tmp/         构建临时目录
├─ projects/<name>/  源码与 build/
├─ out/         APK + <name>-last-build.json（最近一次结果，供 Agent 读取）
└─ downloads/   安装包缓存（可删）
```

默认 root：Windows 上优先 `E:\android-build`（没有 E 盘则 D 盘），macOS / Linux 用 `~/android-build`；可用 `--root` 或 `profiles.json` 里的 `root` 覆盖。

## 二、安装

```bash
# Python 3.9+，无第三方依赖
pip install -e .

# 或者不装，直接跑
python -m android_build_agent.cli list --json
python -m android_build_agent.mcp_server
```

## 三、MCP 接入

### 3.1 Claude Desktop

`claude_desktop_config.json`（Windows 在 `%APPDATA%\Claude\`）：

```json
{
  "mcpServers": {
    "android-build": {
      "command": "python",
      "args": ["-m", "android_build_agent.mcp_server"],
      "cwd": "E:\\android-build\\android-build-agent"
    }
  }
}
```

### 3.2 Cursor / Cline / Continue

同样是「command + args + cwd」三件套，填 `python -m android_build_agent.mcp_server`，工作目录指到本工程根（`profiles.json` 所在目录）。若用 pip 安装过，可直接：

```json
{ "command": "android-build-agent-mcp", "args": [] }
```

### 3.3 工具清单

| 工具 | 参数 | 返回 |
| --- | --- | --- |
| `android_list_profiles` | 无 | 项目列表：repo、branch、task、SDK 版本、工具链是否就绪 |
| `android_install_toolchain` | `profile`（必填）、`root`、`sdkPlatforms`、`buildTools`、`gradleVersion`、`buildAfter` | JDK/Gradle/SDK 路径；`buildAfter=true` 时附构建结果 |
| `android_build` | `profile`（必填）、`task`、`clean`、`updateSource`、`timeoutSec` | `ok` / `exitCode` / `durationSec` / `apks[]`（路径、大小、sha256）/ `logTail` |
| `android_get_build_result` | `profile`（必填） | 最近一次构建结果（读 `out/<name>-last-build.json`） |

调用示例（Agent 视角）：

```json
{"name": "android_build", "arguments": {"profile": "shiye", "clean": true}}
```

返回（节选）：

```json
{
  "ok": true, "exitCode": 0, "durationSec": 76.4,
  "apks": [{"path": "E:\\android-build\\out\\shiye-app-debug.apk", "size": 57147133,
            "sha256": "6c9eec…", "variant": "debug"}],
  "logTail": "BUILD SUCCESSFUL in 1m 15s"
}
```

## 四、CLI 接入（给只有 shell 的 Agent）

```bash
android-build-agent list --json
android-build-agent install shiye --build --json
android-build-agent build shiye --clean --json
android-build-agent artifacts shiye --json
```

规则：**加了 `--json`，stdout 只有一行 JSON，日志全部走 stderr**，Agent 直接 `json.loads(stdout)` 即可。退出码 0 = 成功，1 = 构建失败，2 = 参数错误。

## 五、安全边界（为什么敢给 Agent 调用）

| 边界 | 实现 |
| --- | --- |
| 只能构建登记过的项目 | `profiles.json` 白名单；未知 profile 直接报错并列出可用项 |
| 不能执行任意命令 | task 只允许 `assemble* / bundle* / lint* / compileDebugKotlin / clean`，可带 `:module:` 前缀；含 `; & \| $ \` \n` 直接拒绝 |
| 不会写到系统目录 | 所有路径都由 root 派生；不调用 sudo / 管理员，不改系统环境变量与注册表 |
| 不碰项目原有配置 | `gradle.properties` 只替换/追加 5 个受控键（堆、in-process、并行、workers、androidX），其余原样保留 |
| 可追溯 | 每次构建写 `out/<name>-last-build.json`：耗时、退出码、APK sha256、日志尾部 |

## 六、profiles.json

```json
{
  "defaults": { "sdkPlatforms": ["android-33"], "buildTools": ["33.0.2"],
                "gradleVersion": "7.6.3", "task": "assembleDebug" },
  "projects": {
    "shiye": { "repo": "https://github.com/Tommyface/shiye-android.git", "branch": "main" },
    "myapp": { "repo": "https://github.com/me/myapp.git", "branch": "main",
               "sdkPlatforms": ["android-34"], "buildTools": ["34.0.0"], "gradleVersion": "8.7" },
    "bigapp": { "repo": "https://github.com/me/bigapp.git",
                "sdkPlatforms": ["android-35"], "buildTools": ["35.0.0"],
                "extraSdkPackages": ["ndk;26.1.10909125", "cmake;3.22.1"],
                "gradleVersion": "8.7", "task": ":app:assembleDebug" }
  }
}
```

路径优先级：命令行参数 > profile > defaults > 平台默认。

AGP ↔ Gradle：脚本会从 `build.gradle` / `libs.versions.toml` 读 AGP 版本，低于建议值时在结果里给 `hint`（不阻断构建）。

## 七、给 Agent 的系统提示（可直接粘）

```
你有 Android 构建能力，通过 android_* 工具调用。
- 先 android_list_profiles 看有哪些项目；
- 构建前若工具链未就绪，先 android_install_toolchain；
- 构建用 android_build(profile, clean?)，失败时看返回的 logTail / errorTail；
- 不要尝试传自定义 shell 命令，task 只能是 assembleDebug/assembleRelease/bundleRelease/lint 等。
```

## 八、和 PowerShell 脚本的关系

| 场景 | 用什么 |
| --- | --- |
| 自己在 Windows 上双击编译 | `shiye-install.ps1` / `android-install.ps1` 那套 bat |
| Agent 自动构建、CI 里调用、跨平台 | 本工程（Python） |

两者共用同一份 `profiles.json` 结构，root 目录也一致，可以混用：Agent 构建产出的 APK 同样落在 `out/`。

## 九、已知限制

- 只支持 Gradle 项目（不处理 Ant / Bazel / Flutter 外壳）。
- Release 签名需要项目自己配置好 `signingConfigs`，本工具不代管密钥。
- 首次安装工具链需要下载约 1.1 GB（JDK 320 MB + Gradle 130 MB + SDK 约 600 MB），失败会自动换镜像重试。
- MCP 走 stdio，没有网络端口；需要远程调用时请套 SSH 或自建 HTTP 网关。
*（内容由AI生成，仅供参考）*
