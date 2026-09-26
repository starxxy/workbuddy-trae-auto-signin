<div align="center">

# 🤖 workbuddy-trae-auto-signin

**WorkBuddy / CodeBuddy / Trae CN / Trae Code / Qoder 多平台自动签到脚本（单文件 · 零第三方依赖）**

一个自用的多平台签到小工具：一份 Python 标准库代码，同时照顾 WorkBuddy / CodeBuddy、Trae CN / Trae Code 与 Qoder 五个客户端的每日签到、积分与活动福利领取，并附带一个本地网页控制台。

> CodeBuddy 与 WorkBuddy 共用腾讯 copilot 的账号/积分体系（凭据同路径、接口同
> copilot.tencent.com）；trae code 与 Trae CN 共用 api.trae.cn 的 checkin_credits 接口
> 与 storage.json 凭据。因此页面上两者以独立卡片呈现，但各自复用同一签到引擎。
>
> Qoder 是独立引擎：桌面端没有每日签到接口，等价动作是查询运营活动
> （`openapi.qoder.sh` / campaigns）并把所有「可领取」的积分福利逐个领掉；
> 无活动或均已领取都算成功，页面上单独一张卡片。

</div>

---

> [!WARNING]
> **免责声明（使用前必读）**
>
> - 本项目为**非官方**的个人技术实验工具，**仅用于学习、研究与个人技术验证**，不得用于任何商业用途，也不得用于违反平台规则的大规模自动化行为。
> - 本项目涉及对桌面端本地凭据与请求协议的**逆向分析**，此类方式可能违反相关服务协议，**由此产生的法律风险由使用者自行承担**，作者不承担任何责任。
> - **使用本项目所导致的一切后果——包括但不限于账号封禁、功能限制、积分回收、数据丢失——均由使用者自行承担**，作者不作任何担保。建议保持低频使用（本项目默认每天一次），发现异常请立即停用。
> - 签到接口与解密算法系逆向所得，可能随时变动且不另行通知；请自行遵守相关服务条款。

---

## ✨ 特性

- **单文件、零第三方依赖**：只用 Python 标准库；AES-128-CBC **优先**走系统 `openssl` 子进程（Git for Windows 自带），系统没有 `openssl` 时自动回落到内置的纯 Python 实现，照样跑得起来。
- **多平台，一份代码**：WorkBuddy + CodeBuddy（共用腾讯 copilot 引擎）+ Trae CN + Trae Code（共用 Trae 引擎）+ Qoder（独立引擎：活动福利领取）。
- **幂等安全**：先查状态，未签才领；重复运行不会多领。Qoder 侧同样只领「可领取」状态的活动福利。
- **零 Token**：走 Windows 任务计划程序静默运行，不消耗任何 AI 模型调用。
- **零内置密钥**：只读取你自己本机桌面端的登录凭据，不内嵌、不传输任何第三方密钥。
- **智能汇报**：一行 JSON，`report` 字段是人话汇报。
- **本地网页控制台（可选）**：不想敲命令就 `python webapp.py`，浏览器里看状态、一键签到、翻日志、管定时任务、**设置自动签到时间**、**一键开启开机自启**。控制台直接复用脚本里的签到函数，行为与命令行完全一致。

---

## 📦 分发与安装

把项目地址交给 AI，让它学习项目代码并自行安装程序，泡杯茶等 AI 帮你跑完即可。

---

## 📋 前置条件

- ✅ 已安装并**登录过 WorkBuddy / CodeBuddy 桌面端**（两者同属腾讯 copilot 体系，脚本靠同一份凭据鉴权）。
- ✅ 已安装并**登录过 Trae 桌面端**（Trae CN / Trae Code 同属 Trae 体系，脚本靠 storage.json 里的 `iCubeAuthInfo` 鉴权）。
- ✅ 使用 Qoder 功能：已安装并**登录过 Qoder 桌面端**（脚本读其数据目录里的 `auth.v1.dat` + `Local State` 鉴权；凭据解包依赖 Windows DPAPI，**Qoder 段仅限 Windows**）。
- ✅ 本机有 **Python 3**（任意版本，无需任何第三方包）。
- ⭕ **可选**：本机有 **`openssl`** 命令（Git for Windows 自带，`where openssl` 能查到即可）。有就用它解密（更快）；没有则自动走内置纯 Python AES，**无需安装任何东西**。

---

## ⏰ 每日定时自动化

### 🪟 Windows · 任务计划程序（一键）

在仓库目录下运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\install-windows.ps1                 # 只装 WorkBuddy
powershell -ExecutionPolicy Bypass -File .\install-windows.ps1 -Platform trae  # 只装 Trae CN
powershell -ExecutionPolicy Bypass -File .\install-windows.ps1 -Platform qoder # 只装 Qoder
powershell -ExecutionPolicy Bypass -File .\install-windows.ps1 -Platform both  # 三个都装（推荐）
powershell -ExecutionPolicy Bypass -File .\install-windows.ps1 -Time 08:30     # 改签到时间
```

脚本会自动探测 `pythonw.exe` 和 `signin.py` 的路径，然后注册任务：

| 任务 | 频率 | 干什么 |
|---|---|---|
| `WorkBuddyAutoSignin` | 每天 00:05 | WorkBuddy 签到 + 成长中心，静默写 `signin.log` |
| `TraeAutoSignin` | 每天 00:05 | Trae CN 签到（先查状态、未签才领） |
| `QoderAutoSignin` | 每天 00:05 | Qoder 活动福利领取（先查活动、有可领才领） |

> 为降低风控/封号风险，默认**只保留每天固定一次**的签到任务，不再安装一天多次的补签轮询
> `WorkBuddyGrowthPoll`。想要补签兜底的旧版，其已注册任务可按下表卸载。

这些任务都零 Token、无窗口、开机错过会自动补跑。

**卸载**：

```powershell
Unregister-ScheduledTask -TaskName "WorkBuddyAutoSignin" -Confirm:$false
Unregister-ScheduledTask -TaskName "TraeAutoSignin" -Confirm:$false
Unregister-ScheduledTask -TaskName "QoderAutoSignin" -Confirm:$false
```

---

## 🛠️ 手动运行

```bash
# WorkBuddy 侧
python signin.py auto           # 签到 + 成长中心
python signin.py silent         # 同 auto，但输出写入日志文件
python signin.py status         # 仅查状态（调试）
python signin.py claim          # 仅领取（调试，幂等）

# Trae CN 侧
python signin.py trae           # 签到（先查状态、未签才领）
python signin.py trae status    # 仅查状态（调试）
python signin.py trae claim     # 仅领取（调试）
python signin.py trae silent    # 静默版本

# Qoder 侧
python signin.py qoder          # 领取全部可领活动福利（先查活动、有可领才领）
python signin.py qoder status   # 仅查活动/可领状态（调试）
python signin.py qoder claim    # 仅领取（调试，服务端幂等）
python signin.py qoder silent   # 静默版本

# 一次签三个平台
python signin.py both           # WorkBuddy + Trae + Qoder 都跑
python signin.py both silent    # 静默版本（退出码取三者最差）
```

---

## 🖥️ Web 控制台

不想记命令、也不想翻 JSON 日志的话，可以直接开一个本地网页控制台。

```bash
pip install -r requirements.txt    # 只装一个 Flask，签到脚本本身依旧零依赖
python webapp.py                   # 启动后会自动打开浏览器
```

浏览器地址：**http://127.0.0.1:9001**

### 六个模块

| 模块 | 能干什么 |
|---|---|
| **状态与积分总览** | 五个平台并列显示（两列等高卡片）：今日是否已签、客户端是否检测到、累计积分、连续天数、活动是否启用、凭据是否有效；Qoder 卡显示运营活动数、可领福利数与可领积分 |
| **一键签到 / 补签** | 「签 WorkBuddy」「签 CodeBuddy」「签 Trae CN」「签 Trae Code」「签 Qoder」「全部一起签」「只跑成长中心」按钮，执行中显示加载态，结果内联展示（区分 `ALREADY` / `CLAIMED` / `CLAIM` / `PARTIAL` / `NO_AUTH` / `ERROR`） |
| **日志查看与筛选** | 读真实 `signin.log`，最新在前，可按平台和结果类型筛选，补签轮询行（`trigger: poll`）单独标记；支持手动刷新与自动轮询开关 |
| **定时任务管理** | 列出本机各计划任务（含 `QoderAutoSignin`）的真实状态与下次运行时间，可启停，可改签到时间；尚未安装时直接给出安装命令 |
| **自动签到设置** | 设每日自动签到时间（默认 `09:00`），逐平台显示「已装客户端 / 有凭据 / 可自动签到」就绪状态；到达设定时间后后台进程自动签到，一天一次 |
| **开机自启** | 一键写入 Windows 注册表 Run 键，开机自动用 `pythonw` 在后台拉起本程序（无窗口、不自动开浏览器），配合自动签到实现"开机即可、无需手动" |

### 命令行参数

| 参数 | 说明 |
|---|---|
| `--host` | 监听地址，默认 `127.0.0.1`。填非回环地址会被拒绝 |
| `--port` | 监听端口，默认 `9001` |
| `--allow-remote` | 真的想暴露到局域网时的显式解锁开关，必须配合 `--host 0.0.0.0` 使用 |
| `--no-browser` | 启动后不自动打开浏览器（开机自启即以该参数拉起） |

### 接口一览

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/api/status` | 五平台状态、积分汇总与客户端检测结果 |
| `GET` | `/api/health` | 健康检查与运行环境信息 |
| `POST` | `/api/signin/<platform>` | 签到，`platform` 取 `workbuddy` / `codebuddy` / `trae` / `traecode` / `qoder` / `both`（`both` 按引擎各跑一次，避免同体系重复领取） |
| `POST` | `/api/growth` | 单独跑一次 WorkBuddy 成长中心全套 |
| `GET` | `/api/logs` | 读日志，支持 `limit` / 平台 / 结果类型筛选 |
| `GET` | `/api/tasks` | 各计划任务（WorkBuddy / Trae / Qoder）的安装状态、启停状态、下次运行时间 |
| `POST` | `/api/tasks/<name>/toggle` | 启用 / 禁用任务 |
| `POST` | `/api/tasks/<name>/time` | 改任务签到时间（仅每日签到类任务可改） |
| `GET` | `/api/autosignin` | 当前的自动签到配置、各平台就绪状态与开机自启状态 |
| `POST` | `/api/autosignin` | 保存自动签到开关与每日时间（写 `config.json`） |
| `POST` | `/api/autostart` | 开 / 关开机自启（写注册表 Run 键） |

### 几个说明

- **后端不复制签到逻辑**：网页的所有查询与领取都直接 `import signin` 调用其中的函数（`run_daily` / `run_growth` / `trae_status` / `trae_claim` / `qoder_campaigns` / `qoder_claim` 等）。接口地址、重试、幂等判定、错误归类只有一份实现，所以**命令行和网页的行为永远一致**，不会出现「CLI 说已签、网页说还能领」的分叉。
- **端口被占用**：本机若已有别的程序占了 `9001`，启动会报「访问权限不允许」。换个端口即可：`python webapp.py --port 9010`。
- **异常不会白屏**：所有后端异常都被翻译成结构化 JSON（形如 `{"ok": false, "reason": "TASK_MISSING", "report": "..."}`），页面用中文提示，不会出现 500 错误页。例如任务没装时点「改时间」，会直接告诉你「任务尚未在本机注册，请先运行 install-windows.ps1」。
- **默认只对本机开放**：只监听 `127.0.0.1`，并校验请求的 `Origin`，挡掉其它网页发起的跨站请求。要给别人访问得自己加 `--allow-remote`，风险自负。
- **自动签到需要程序常驻**：自动签到由 webapp.py 后台线程在设定时间触发。想"开机自动运行到点自动签"，请在网页「开机自启」打开开关（会写入注册表 Run 键），或用别的方式让 `python webapp.py` 常驻。到点后程序按平台逐一会签：客户端未安装的记为「未检测到 … 跳过」，装了的走正常签到，一天只触发一次。

---

## 🔧 配置

| 环境变量 | 作用 |
|---|---|
| `WORKBUDDY_AUTH_FILE` | 自动探测失败时，手动指定 WorkBuddy 凭据文件路径 |
| `WORKBUDDY_EXE` | 手动指定 `WorkBuddy.exe` 完整路径。凭据被桌面端加密时，脚本靠它以 `ELECTRON_RUN_AS_NODE` 模式取解密密钥；自动探测失败（装在非常规目录）时设这个 |
| `WORKBUDDY_SIGNIN_LOG` | `silent` 模式下日志文件路径（默认 `signin.log`） |
| `WORKBUDDY_BUDGET_SECONDS` | 单次运行的网络请求时间预算。非法值会被夹到安全区间 |
| `WORKBUDDY_GROWTH_LOG_EMPTY` | 设为 `1` 时，`silent-poll` 连空跑也写日志 |
| `TRAE_AUTH_FILE` | 手动指定 Trae `storage.json` 路径（多路径用 `;` 分隔，第一个存在的胜出） |
| `TRAE_STORAGE_DIR` | 手动指定 Trae 存储目录（脚本自动拼接 `User/globalStorage/storage.json`） |
| `QODER_AUTH_FILE` | 手动指定 Qoder `auth.v1.dat` 完整路径（覆盖自动探测） |
| `QODER_STORAGE_DIR` | 手动指定 Qoder 数据目录（须同时含 `auth.v1.dat` 与 `Local State`） |
| `QODER_API_BASE` | 强制指定 Qoder API 主机（默认按序探测 `openapi.qoder.sh` → `openapi.qoder.com.cn`，先通者定） |
| `OPENSSL_BIN` | 指定 `openssl` 可执行文件的完整路径（可选）。不设置或路径无效时，自动回落到内置纯 Python AES |

---

## 🧪 排错

| 现象 | 处理 |
|---|---|
| `NO_AUTH / 未找到 Trae 桌面端 storage.json` | 先登录一次 Trae 桌面端；或设置 `TRAE_AUTH_FILE` 指向完整路径 |
| `ERROR / 加载 Trae 会话失败: 未找到 openssl` | 旧版本才有：现在没有 `openssl` 会自动走内置纯 Python AES，**无需安装**。想强制指定某个 `openssl`（比如 Git 自带的），设 `OPENSSL_BIN` 为它的完整路径 |
| `ERROR / 解密后不是合法 JSON` | storage.json 已损坏或加密格式变了——退出并重新登录 Trae 桌面端 |
| `ERROR / storage.json 缺少 iCubeAuthInfo 键` | 桌面端可能刚装完还没登录 |
| `ENCRYPTED_AUTH / 登录凭据已被 WorkBuddy 桌面端加密，本次未能解封` | 最新版客户端把 `accessToken` 换成了 `$wbEncrypted` 信封，脚本需要 `WorkBuddy.exe` 来取解密密钥。按 `report` 里给的原因处理：找不到主程序就设 `WORKBUDDY_EXE` 指向它；密钥指纹不一致或标签校验失败通常是客户端刚升级，重登一次桌面端即可 |
| `NO_SESSION / HTTP 401|403`（WorkBuddy 侧） | WorkBuddy 登录态确实过期——重新登录 WorkBuddy 桌面端。（凭据加密导致的 401 已不再走这里，会报上面的 `ENCRYPTED_AUTH`） |
| `NO_AUTH / 未找到 Qoder 桌面端数据目录` | 先登录一次 Qoder 桌面端；或设置 `QODER_AUTH_FILE` 指向 `auth.v1.dat`（解密依赖 Windows DPAPI，Qoder 段仅限 Windows） |
| `NO_SESSION / HTTP 401|403`（Qoder 侧） | 遍历全部候选主机均鉴权失败——重新登录 Qoder 桌面端。（国际版与国内版是两套账号体系，token 不通用） |
| `PARTIAL / 部分领取成功`（Qoder 侧） | 某个福利领取失败即中止（防止废请求连环），下次运行会从未领完的继续 |
| `INACTIVE / 签到活动未开启` | 非签到季，属正常，无需处理；Qoder 侧含义为「当前无运营活动」 |
| `NETWORK / 网络不可达` | 断网或服务端不可用，非登录问题；脚本内置退避重试 |
| `TIMEOUT / 已达本次运行时间预算` | 网络严重超时导致预算耗尽，已领到的部分照常记录 |
| `ERROR / 登录凭据文件不是合法 JSON` | 本地凭据文件损坏——重新登录 WorkBuddy 桌面端 |
| 想看各平台的原始返回 | `python signin.py status`、`python signin.py trae status` 和 `python signin.py qoder status` |

---

## 🔐 安全与隐私

- 脚本只读取**你自己本机**的桌面端会话文件，不含、不内嵌、不传输任何第三方密钥。
- 永远不会打印 `accessToken`、Trae JWT 或 Qoder 会话 token，`Authorization` 头不会出现在日志里。
- AES 解密**优先**走系统 `openssl` 子进程（密码学库由操作系统维护，不重复造轮子）；系统没有 `openssl` 时回落到**内置的纯 Python AES-128 实现**，该实现用 NIST FIPS-197 / SP 800-38A 标准测试向量逐字节校验过。
- WorkBuddy 新版凭据的 AES-256-GCM 同样走**内置纯 Python 实现**（AES-256 正向密码 + CTR + GHASH，用 FIPS-197 C.3 与 GCM 规范测试向量校验），失败才回落给 `WorkBuddy.exe` 自带的 Node crypto；at-rest 密钥与 token 都只在内存中流转，不落盘、不打印。
- Qoder 凭据（`auth.v1.dat`）是 Chromium os_crypt 封装：先用 **Windows DPAPI** 解出 `Local State` 里的 at-rest 密钥，再用内置 AES-256-GCM 解密会话；令牌临近过期时只调 refresh 更新**内存**，不回写任何本地文件。
- 可安全 fork、分享、在自己机器上运行——它只作用于**你自己的**登录态。

---

## 📄 项目结构

```
workbuddy-trae-auto-signin/
├── signin.py                          # 主脚本（WorkBuddy + Trae CN + Qoder，纯标准库；openssl 可选，缺失时用内置纯 Python AES）
├── webapp.py                          # 本地 Web 控制台后端（Flask，只做编排、复用 signin.py）
├── requirements.txt                   # Web 控制台的依赖（只有 Flask）
├── web/
│   └── index.html                     # 控制台前端（单文件，内联 CSS/JS，无外部资源）
├── install-windows.ps1                # Windows 一键安装（-Platform workbuddy|trae|qoder|both）
└── README.md                          # 本文档
```

> `webapp.py` / `requirements.txt` / `web/` 只服务于网页控制台。只用命令行的话，删掉它们不影响 `signin.py` 与已注册的计划任务。

---

## 📄 协议

本项目采用 MIT 协议发布，可自由使用、修改与分发。使用前请先阅读文首免责声明：仅限学习研究，使用风险（含封号等后果）由使用者自行承担。
