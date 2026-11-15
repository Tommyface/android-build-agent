# android-build-agent 使用说明

> 把 Android 构建能力做成 AI Agent 可直接调用的工程：**MCP 服务 + CLI** 两条入口，共用一份 `profiles.json`。
> 仓库：https://github.com/Tommyface/android-build-agent
> PyPI：https://pypi.org/project/android-build-agent/（当前 1.0.3）
> 要求：Python 3.9+，无第三方依赖，Windows / macOS / Linux 均可

---

## 一、它解决什么问题

Agent 要「编译一个 Android 项目」，通常只能让模型自己拼 `gradlew` 命令，风险是：路径错、参数被注入、缓存把 C 盘塞满、SDK 版本不对。

这个工程把这些收进白名单：

- **项目白名单**：只能构建 `profiles.json` 里登记过的项目，不能临时指定任意路径或仓库
- **任务白名单**：`assembleDebug / assembleRelease / bundleDebug / bundleRelease / lint / compileDebugKotlin / clean`，支持 `:app:` 前缀；含 `; & | $ \`` 直接拒绝
- **路径隔离**：JDK、Gradle、SDK、依赖缓存、临时文件、构建产物全在 root 下（Windows 默认优先 `E:\android-build`）
- **结果可追溯**：每次构建写 `out/<项目名>-last-build.json`，含退出码、耗时、APK 的 sha256、日志尾部

---

## 二、安装

### 2.1 方式 A：从 PyPI（推荐）

```bash
pip install android-build-agent
```

装完得到两个命令：

| 命令 | 用途 |
| --- | --- |
| `android-build-agent` | CLI，给只会跑 shell 的 Agent 或人用 |
| `android-build-agent-mcp` | MCP 服务（stdio），给 Claude Desktop / Cursor / Cline 用 |

### 2.2 方式 B：从源码

```bash
git clone https://github.com/Tommyface/android-build-agent.git
cd android-build-agent
pip install -e .
```

---

## 三、第一步：写 profiles.json

放在**你调用时的工作目录**（或 `--profiles` 指定路径，环境变量 `ANDROID_BUILD_PROFILES` 也认）。

```json
{
  "defaults": {
    "sdkPlatforms": ["android-33"],
    "buildTools": ["33.0.2"],
    "gradleVersion": "7.6.3",
    "task": "assembleDebug"
  },
  "projects": {
    "shiye": {
      "repo": "https://github.com/Tommyface/shiye-android.git",
      "branch": "main"
    },
    "myapp": {
      "repo": "https://github.com/me/myapp.git",
      "branch": "main",
      "sdkPlatforms": ["android-34"],
      "buildTools": ["34.0.0"],
      "gradleVersion": "8.7"
    },
    "bigapp": {
      "repo": "https://github.com/me/bigapp.git",
      "sdkPlatforms": ["android-35"],
      "buildTools": ["35.0.0"],
      "extraSdkPackages": ["ndk;26.1.10909125", "cmake;3.22.1"],
      "gradleVersion": "8.7",
      "task": ":app:assembleDebug"
    }
  }
}
```

| 字段 | 说明 |
| --- | --- |
| `repo` | 必填。GitHub 地址最省事（没装 git 也能走 zip 下载） |
| `branch` | 默认 `main` |
| `sdkPlatforms` / `buildTools` | 项目需要的 SDK 组件，缺了会自动 `sdkmanager` 装 |
| `extraSdkPackages` | NDK / CMake 等额外包 |
| `gradleVersion` | 必须与项目 AGP 匹配（见第五节） |
| `task` | 默认构建任务，可写 `:app:assembleDebug` |
| `root` | 可选，覆盖安装根目录 |

取值优先级：**命令行参数 > profile > defaults > 平台默认**。

---

## 四、接入方式

### 4.1 MCP（Claude Desktop / Cursor / Cline）

配置文件里加一个 server：

```json
{
  "mcpServers": {
    "android-build": {
      "command": "android-build-agent-mcp",
      "args": [],
      "cwd": "E:\\android-build"
    }
  }
}
```

> `cwd` 指向 `profiles.json` 所在目录。若用 `python -m` 方式启动：`command` 填 `python`，`args` 填 `["-m","android_build_agent.mcp_server"]`。

暴露的 4 个工具：

| 工具 | 参数 | 返回 |
| --- | --- | --- |
| `android_list_profiles` | 无 | 项目列表：repo、branch、task、SDK 版本、工具链/源码是否就绪 |
| `android_install_toolchain` | `profile`（必填）、`root`、`sdkPlatforms`、`buildTools`、`gradleVersion`、`buildAfter` | JDK/Gradle/SDK 路径；`buildAfter=true` 会顺带构建 |
| `android_build` | `profile`（必填）、`task`、`clean`、`updateSource`、`timeoutSec` | `ok` / `exitCode` / `durationSec` / `apks[]`（路径、大小、sha256）/ `logTail` |
| `android_get_build_result` | `profile`（必填） | 最近一次构建结果（读 `out/<name>-last-build.json`） |

给 Agent 的系统提示可以直接用：

```
你有 Android 构建能力，通过 android_* 工具调用。
1. 先 android_list_profiles 看有哪些项目；
2. 工具链没就绪就先 android_install_toolchain；
3. 构建用 android_build(profile, clean?)，失败看 logTail / errorTail；
4. 不要传自定义 shell 命令，task 只能是 assembleDebug / assembleRelease / bundleRelease / lint 等。
```

### 4.2 CLI（给只能跑 shell 的 Agent）

```bash
# 看项目
android-build-agent list --json

# 装工具链（可选顺便构建）
android-build-agent install shiye --build --json

# 增量构建
android-build-agent build shiye --json

# clean 后重编
android-build-agent build shiye --clean --json

# 打 release（项目自己配好签名）
android-build-agent build shiye --task assembleRelease --json

# 读最近一次结果
android-build-agent artifacts shiye --json
```

**约定**：加 `--json` 时 **stdout 只有一行 JSON，日志全部走 stderr**，Agent 直接 `json.loads(stdout)`；退出码 `0` 成功、`1` 构建失败、`2` 参数错误。

常用参数：

| 参数 | 作用 |
| --- | --- |
| `--profiles PATH` | 指定 profiles.json（可放在子命令前后） |
| `--root DIR` | 覆盖安装根目录 |
| `--task NAME` | 覆盖构建任务 |
| `--clean` | 先 clean |
| `--update` | 构建前 `git pull` |
| `--no-install` | 缺工具链时不自动安装，直接报错 |
| `--timeout SEC` | Gradle 超时，默认 3600 |

---

## 五、AGP ↔ Gradle ↔ JDK 对照

| AGP | 最低 Gradle | JDK |
| --- | --- | --- |
| 7.0 – 7.4 | 7.5 | 11（17 也可） |
| 8.0 / 8.1 | 8.0 | **17** |
| 8.2 | 8.2 | 17 |
| 8.3 | 8.4 | 17 |
| 8.4 | 8.6 | 17 |
| 8.5 / 8.6 | 8.7 | 17 |

工具会自动从 `build.gradle` / `build.gradle.kts` / `libs.versions.toml` 里读 AGP 版本，低于建议值时在返回里给 `hint`（提示，不阻断）。JDK 固定用 Temurin 17，AGP 7.x 与 8.x 都覆盖。

---

## 六、目录结构（装完之后）

```
<root>/
├─ toolchain/
│  ├─ jdk-17/            Temurin 17（约 320 MB，所有项目共用）
│  ├─ gradle-<版本>/      按 profile 需要下载
│  └─ sdk/               platforms + build-tools（按项目增删）
├─ gradle-home/          依赖缓存（GRADLE_USER_HOME，不落系统盘）
├─ tmp/                  构建临时目录（java.io.tmpdir / TMP / TEMP）
├─ projects/<名字>/       源码与 build/
├─ out/                  APK + <名字>-last-build.json
└─ downloads/            安装包缓存（可删）
```

Windows 上默认 root 依次尝试 `E:\android-build` → `D:` → `F:` → 用户目录，所以 8 GB 内存、C 盘紧张的老机器可以直接用。

---

## 七、发版检查清单（维护本项目时）

1. 改两处版本号：`pyproject.toml` 的 `version`、`android_build_agent/__init__.py` 的 `__version__`
2. 本地跑：
   ```bash
   python tests/test_metadata.py    # 版本一致 + __init__ 非空 + 入口可达
   python tests/test_config.py      # 14 个单测
   python tests/smoke_mcp.py        # MCP 握手 + 安全拦截
   ```
3. 打 tag 推送：`git tag v1.0.x && git push origin v1.0.x`
4. 等两条流水线绿：`CI`（3.10/3.12）、`publish`（构建 + Trusted Publishing 发布）
5. 上 PyPI 确认版本，必要时把坏版本 **Yank**（PyPI 不支持删除已发布版本）

> 反面案例：1.0.2 曾因改版本号时把 `__init__.py` 写成 0 字节，包里没有 `__version__`，`mcp_server` 一启动就崩。现在 `test_metadata.py` 专门守这条，`publish.yml` 也会在构建前拦一次。

---

## 八、常见问题

| 现象 | 处理 |
| --- | --- |
| `未找到项目 'xxx' 的 repo` | profiles.json 里没这个项目，或 `--profiles` 指错文件；返回里会列出可用项目 |
| `task 不在白名单内` | 只能用 assemble*/bundle*/lint*/compileDebugKotlin/clean，可带 `:module:` 前缀 |
| 下载 JDK 失败 | 依次试清华 → 腾讯 → GitHub 镜像；都失败就手动放到 `<root>/downloads/jdk17.zip` |
| Gradle 版本不匹配报错 | 按第五节把 `gradleVersion` 调到对应版本，工具会自动下载 |
| 编译进程突然消失、日志停半句 | 内存不足：把 `gradle.properties` 的 `-Xmx1536m` 降到 `-Xmx1024m`，`org.gradle.workers.max=1` |
| `SDK location not found` | 删掉项目里的 `local.properties` 重新跑 `install`（每次都会重写） |
| 想换盘 | `--root "F:\android-build"` 或在 profile 里写 `root` |
| 需要远程调用 MCP | MCP 是 stdio，本机进程；远程请套 SSH 隧道或自建 HTTP 网关 |

---

## 九、安全边界（为什么敢让 Agent 直接调）

| 边界 | 实现 |
| --- | --- |
| 只能构建登记过的项目 | profiles 白名单，未知项目直接报错 |
| 不能执行任意命令 | 任务白名单 + 非法字符拦截（`; & \| $ \`` 与换行） |
| 不写系统目录 | 全部路径由 root 派生；不调 sudo / 管理员，不改注册表 |
| 不动项目原有配置 | `gradle.properties` 只替换/追加 5 个受控键，其余原样保留 |
| 可追溯 | `out/<name>-last-build.json` 记录退出码、耗时、APK sha256、日志尾部 |
