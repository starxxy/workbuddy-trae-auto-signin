#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WorkBuddy + Trae CN 签到脚本的本地 Web 控制台（Flask 后端）。

设计原则：**只做编排，不复制签到逻辑**。

所有状态查询与领取动作都直接调用 ``signin.py`` 里已有的函数
（``find_auth_file`` / ``load_session_retry`` / ``build_headers`` / ``run_daily`` /
``run_growth`` / ``find_trae_storage_file`` / ``load_trae_session`` /
``build_trae_headers`` / ``trae_status`` / ``trae_claim``），接口地址、重试策略、
幂等判定、错误归类全都只有一份实现——命令行与网页的行为因此完全一致，不会出现
"CLI 说已签、网页说能领"这种分叉。

服务只监听 127.0.0.1（可用 --host 改，但非回环地址需要显式 --allow-remote 才放行），
所有异常都会被翻译成结构化 JSON，绝不把 500 白屏丢给浏览器。

启动：
    pip install -r requirements.txt
    python webapp.py
然后浏览器打开 http://127.0.0.1:9000
"""

import argparse
import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from urllib.parse import urlparse

try:
    import winreg
except ImportError:  # pragma: no cover - 非 Windows 无需改注册表
    winreg = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

try:
    import signin
except Exception as _e:  # pragma: no cover - 只在文件被拆散时触发
    sys.stderr.write(
        "无法导入 signin.py（它应与 webapp.py 放在同一目录）：%s\n" % _e)
    raise SystemExit(2)

try:
    from flask import Flask, jsonify, request, send_from_directory
    from werkzeug.exceptions import HTTPException
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "缺少依赖 Flask。请先执行：\n\n    pip install -r requirements.txt\n\n"
        "（签到脚本本身不需要它，只有网页控制台需要。）\n")
    raise SystemExit(2)


# ============================================================
# 常量
# ============================================================
SIGNIN_DIR = os.path.dirname(os.path.abspath(signin.__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")

# 与 signin.py 中 run_auto 使用完全相同的两个端点，集中在这里只为少写几遍字符串
STATUS_PATH = "/v2/billing/meter/checkin-activity-status"
CLAIM_PATH = "/v2/billing/meter/daily-checkin"

DEFAULT_PORT = 9001
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1")

# 日志页不再无脑全量读盘：单文件留最后 2MB 足够（一天七轮日志，2MB 相当于几个月）
LOG_TAIL_BYTES = 2 * 1024 * 1024
LOG_DEFAULT_LIMIT = 200
LOG_MAX_LIMIT = 1000

# 与 install-windows.ps1 注册的任务名一一对应，改一处要改另一处
TASKS = (
    {"name": "WorkBuddyAutoSignin", "platform": "workbuddy", "kind": "signin",
     "label": "WorkBuddy 每日签到", "note": "每天固定时间跑一次 auto"},
    {"name": "TraeAutoSignin", "platform": "trae", "kind": "signin",
     "label": "Trae CN 每日签到", "note": "每天固定时间跑一次 trae silent"},
    {"name": "QoderAutoSignin", "platform": "qoder", "kind": "signin",
     "label": "Qoder 活动领取", "note": "每天固定时间跑一次 qoder silent"},
)
TASK_NAMES = tuple(t["name"] for t in TASKS)

# 时间格式：只接受 24 小时的 HH:MM，用于拼进 schtasks 命令行前先做白名单校验
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
# 日志行格式由 signin.emit 决定：[YYYY-MM-DD HH:MM:SS] {json}
LINE_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s*(.*)$")

# 浏览器侧的结果词表：把 signin 的各种 result 归一成 UI 需要展示的语义
RESULT_LABELS = {
    "CLAIMED": "领取成功",
    "CLAIM": "领取成功",
    "PARTIAL": "部分领取成功",
    "ALREADY": "今日已签",
    "INACTIVE": "活动未开放",
    "NO_AUTH": "缺少登录凭据",
    "NO_SESSION": "登录态失效",
    "NETWORK": "网络不可达",
    "TIMEOUT": "时间预算耗尽",
    "ERROR": "执行失败",
    "UNKNOWN": "未识别的返回",
    "INFO": "信息",
    "BUSY": "正在执行",
}

# 一次签到会阻塞到跑完（含重试），同一时刻只允许一个，否则两个线程会互相踩
# signin 模块级的预算时钟（_started_at / _budget_seconds 是全局量）
_OP_LOCK = threading.Lock()


# ============================================================
# Flask 应用
# ============================================================
app = Flask(__name__, static_folder=None)
app.config["JSON_AS_ASCII"] = False          # Flask < 2.3
try:
    app.json.ensure_ascii = False            # Flask >= 2.2
except Exception:                            # pragma: no cover
    pass


def ok(data=None, **extra):
    body = {"ok": True}
    if data is not None:
        body["data"] = data
    body.update(extra)
    return jsonify(body)


def err(report, reason="ERROR", status=400, **extra):
    body = {"ok": False, "reason": reason, "report": report}
    body.update(extra)
    return jsonify(body), status


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _origin_ok():
    """挡掉第三方网页对我们发起的跨站请求。

    服务只绑回环，但浏览器依然允许任意网页向 http://127.0.0.1:9000 发简单请求；
    带 Origin 头且来源不是本机时直接拒绝，避免"误点一个网页就把签到签了"。
    """
    origin = request.headers.get("Origin")
    if not origin:
        return True
    host = (urlparse(origin).hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1")


def _guard():
    """POST 路由的统一前置检查，返回 None 表示放行，否则返回要直接返回的响应。"""
    if not _origin_ok():
        return err("已拒绝来自外部网页的请求（服务仅限本机使用）", "FORBIDDEN", 403)
    return None


# ============================================================
# 凭据加载（与 _run 完全相同的顺序与判定，不另起一套）
# ============================================================
class AuthMissing(Exception):
    """凭据层面就进行不下去：文件不存在、解密失败、token 缺失等。"""

    def __init__(self, code, report, **extra):
        Exception.__init__(self, report)
        self.code = code
        self.report = report
        self.extra = extra


def _wb_endpoint(session):
    """WorkBuddy 端点：凭据里带了就优先用，否则回落默认值。

    与 _run 里那行 ``((session.get("auth") or {}).get("endpoint") or DEFAULT_ENDPOINT)``
    保持等价——自建网关的用户不会因为换到网页就打到官方端点。
    """
    raw = (session.get("auth") or {}).get("endpoint") or signin.DEFAULT_ENDPOINT
    return str(raw).rstrip("/")


def _load_wb(timeout_hint=False):
    """返回 (auth_file, session, headers, endpoint)，失败抛 AuthMissing。"""
    auth_file, looked_in = signin.find_auth_file()
    if not auth_file or not os.path.exists(auth_file):
        override = os.environ.get("WORKBUDDY_AUTH_FILE")
        if override:
            report = "WORKBUDDY_AUTH_FILE 指向的文件不存在：%s" % override
        else:
            report = ("未找到 WorkBuddy 登录凭据。请先在本机登录 WorkBuddy 桌面端；"
                      "或设置环境变量 WORKBUDDY_AUTH_FILE 指向 workbuddy-desktop.info。")
        raise AuthMissing("NO_AUTH", report, looked_in=looked_in)

    try:
        session = signin.load_session_retry(auth_file)
    except Exception as e:
        raise AuthMissing(
            "ERROR",
            "读取登录凭据失败（%s: %s），请重新登录 WorkBuddy 桌面端"
            % (type(e).__name__, e),
            credential_file=auth_file)

    try:
        headers = signin.build_headers(session)
    except ValueError as e:
        # build_headers 对 NO_SESSION 场景抛的就是 ValueError("NO_SESSION: ...")
        raise AuthMissing("NO_SESSION", str(e), credential_file=auth_file)
    except Exception as e:
        raise AuthMissing(
            "ERROR",
            "构建请求头失败（%s: %s）" % (type(e).__name__, e),
            credential_file=auth_file)

    return auth_file, session, headers, _wb_endpoint(session)


def _load_trae():
    """返回 (storage_path, session, headers)，失败抛 AuthMissing。"""
    storage_path, looked_in = signin.find_trae_storage_file()
    if not storage_path:
        raise AuthMissing(
            "NO_AUTH",
            "未找到 Trae 桌面端 storage.json。请先登录 Trae 桌面端；"
            "或设置环境变量 TRAE_AUTH_FILE 指向该文件。",
            looked_in=looked_in)

    try:
        session = signin.load_trae_session(storage_path)
        headers = signin.build_trae_headers(session)
    except Exception as e:
        raise AuthMissing(
            "ERROR",
            "加载 Trae 会话失败（%s: %s）" % (type(e).__name__, e),
            credential_file=storage_path)

    return storage_path, session, headers


def _load_qoder():
    """返回 (数据目录, session)，失败抛 AuthMissing。

    与 signin._run_qoder 同口径：token 临近过期先尝试刷新；刷新失败也带着旧
    token 上路，让服务端的 401 说话，比我们本地猜时钟偏差可靠。刷新只更新内存
    里的 session，不回写磁盘。
    """
    qdir, looked_in = signin.find_qoder_dir()
    if not qdir:
        raise AuthMissing(
            "NO_AUTH",
            "未找到 Qoder 桌面端数据目录（auth.v1.dat + Local State）。"
            "请先登录 Qoder 桌面端；或设置环境变量 QODER_AUTH_FILE 指向该文件。",
            looked_in=looked_in)

    try:
        session = signin.load_qoder_session(qdir)
    except Exception as e:
        raise AuthMissing(
            "ERROR",
            "加载 Qoder 会话失败（%s: %s）" % (type(e).__name__, e),
            credential_file=qdir)

    exp = session.get("expires_at")
    if exp and exp - signin.QODER_EXPIRY_MARGIN < time.time():
        try:
            signin.qoder_refresh(session)
        except Exception:
            pass

    return qdir, session


def _begin_budget(action):
    """重置 signin 模块级的时间预算时钟。

    webapp 是直接调用下层函数（而非走 _run），signin 里原本由 _run 负责调用的
    _start_budget 就得由我们补上：否则一次长驻进程里第一轮跑完 420s 预算后，
    后续所有请求都会被判成"时间预算耗尽"。名字带下划线但它就是这个用途。
    """
    starter = getattr(signin, "_start_budget", None)
    if callable(starter):
        starter(action)


# ============================================================
# 平台注册表（插件式：一个"引擎"可被多个平台复用）
# ============================================================
# 调研结论：CodeBuddy 与 WorkBuddy 共用腾讯 copilot 的账号/积分体系（凭据同路径，
# 接口同 copilot.tencent.com）；trae code 与 Trae CN 共用 api.trae.cn 的
# checkin_credits 接口与 storage.json 凭据。所以这里把它们建模成"引擎复用 +
# 独立平台卡片"，并在 UI 标注"同体系"，不臆造独立的新接口。
PROVIDERS = (
    dict(id="workbuddy", name="WorkBuddy", engine="wb", sub="tencent copilot",
         appdata_names=("WorkBuddy", "CodeBuddyExtension"),
         exec_names=("WorkBuddy\\WorkBuddy.exe",)),
    dict(id="codebuddy", name="CodeBuddy", engine="wb",
         sub="copilot.tencent.com（同体系）",
         appdata_names=("CodeBuddy",),
         exec_names=("CodeBuddy\\CodeBuddy.exe",)),
    dict(id="trae", name="Trae CN", engine="trae",
         sub="trae.cn / checkin_credits",
         appdata_names=("TRAE SOLO CN", "TRAE CN", "Trae CN", "TRAE", "Trae"),
         exec_names=("Trae\\Trae.exe", "Trae IDE\\Trae.exe")),
    dict(id="traecode", name="Trae Code", engine="trae",
         sub="api.trae.cn（同体系）",
         appdata_names=("TRAE Code", "Trae Code", "TraeCode", "TraeCodeExtension",
                        "TRAE SOLO CN", "TRAE CN", "Trae CN", "TRAE", "Trae"),
         exec_names=("Trae\\Trae.exe", "Trae Code\\TraeCode.exe")),
    dict(id="qoder", name="Qoder", engine="qoder",
         sub="qoder.sh / campaigns",
         appdata_names=("com.qoder.app.stable", "Qoder", "qoder"),
         exec_names=("Qoder\\Qoder.exe", "Qoder IDE\\Qoder IDE.exe")),
)
PROVIDER_MAP = {p["id"]: p for p in PROVIDERS}
PLATFORM_IDS = tuple(p["id"] for p in PROVIDERS)


def provider_readiness(prov):
    """探测某个平台"客户端是否安装"，返回 (installed, found_path, looked_in)。"""
    found, looked = signin.is_product_installed(
        prov.get("appdata_names") or (), prov.get("exec_names") or ())
    return found is not None, found, looked


def provider_status(pid):
    """按平台 id 取一张可用/不可用状态卡片（含就绪判定字段）。"""
    prov = PROVIDER_MAP[pid]
    installed, found, _looked = provider_readiness(prov)
    if prov["engine"] == "wb":
        card = wb_status(pid, prov["name"])
    elif prov["engine"] == "trae":
        card = trae_status(pid, prov["name"])
    else:
        card = qoder_status(pid, prov["name"])
    card["installed"] = installed
    card["engine"] = prov["engine"]
    card["sub"] = prov["sub"]
    return card


# ============================================================
# 状态总览
# ============================================================
def wb_status(plat="workbuddy", name="WorkBuddy"):
    """腾讯 copilot 引擎的平台卡片数据。拿不到凭据不算错误，照常返回结构化不可用状态。"""
    card = {"platform": plat, "name": name, "ok": False}
    try:
        auth_file, _session, headers, endpoint = _load_wb()
    except AuthMissing as e:
        card.update(reason=e.code, report=e.report)
        card.update(e.extra)
        return card

    card["credential_file"] = auth_file
    _begin_budget("web-status")
    try:
        code, body = signin.post(endpoint + STATUS_PATH, headers, retry=True)
    except Exception as e:
        card.update(reason="ERROR",
                    report="查询失败（%s: %s）" % (type(e).__name__, e))
        return card

    if code == signin.CODE_BUDGET_OUT:
        card.update(reason="TIMEOUT", report="已达时间预算，未取得状态")
        return card
    if code == signin.CODE_NO_NETWORK:
        card.update(reason="NETWORK",
                    report="网络不可达：%s" % ((body or {}).get("error") or ""))
        return card
    if code in (401, 403):
        card.update(reason="NO_SESSION",
                    report="登录态已失效（HTTP %s），请重新登录 WorkBuddy 桌面端" % code)
        return card
    if not (200 <= code < 300):
        card.update(reason="ERROR",
                    report="签到状态接口返回异常（HTTP %s）" % code, http=code)
        return card

    status = body if isinstance(body, dict) else {}
    active = signin.dig(status, "active")
    card.update(
        ok=True,
        active=(active is not False),
        activity_name=signin.dig(status, "activity_name"),
        checked_in=bool(signin.dig(status, "today_checked_in") in (True, 1)),
        credits=signin.dig(status, "total_credits"),
        streak_days=signin.dig(status, "streak_days"),
        is_streak_day=signin.dig(status, "is_streak_day"),
        next_streak_day=signin.dig(status, "next_streak_day"),
        endpoint=endpoint,
    )
    if not card["active"]:
        card["report"] = "签到活动未开启" + (
            "（%s）" % card["activity_name"] if card["activity_name"] else "")
    return card


def trae_status(plat="trae", name="Trae CN"):
    """Trae 引擎的平台卡片数据。"""
    card = {"platform": plat, "name": name, "ok": False}
    try:
        storage_path, _session, headers = _load_trae()
    except AuthMissing as e:
        card.update(reason=e.code, report=e.report)
        card.update(e.extra)
        return card

    card["credential_file"] = storage_path
    _begin_budget("web-status")
    try:
        st = signin.trae_status(headers)
    except Exception as e:
        card.update(reason="ERROR",
                    report="查询失败（%s: %s）" % (type(e).__name__, e))
        return card

    if "error" in st:
        code = st.get("error")
        if code == signin.CODE_NO_NETWORK:
            card.update(reason="NETWORK",
                        report="网络不可达：%s" % ((st.get("body") or {}).get("error") or ""))
        else:
            card.update(reason="ERROR",
                        report="签到状态查询失败（HTTP %s）" % code, http=code)
        return card

    card.update(ok=True, checked_in=bool(st.get("checked_in")),
                enable=bool(st.get("enable", True)),
                credits=st.get("credits"))
    if not card["enable"]:
        card["report"] = "签到活动暂未开放"
    return card


def qoder_status(plat="qoder", name="Qoder"):
    """Qoder 引擎的平台卡片数据（活动福利型：无每日签到，报活动数与可领数）。"""
    card = {"platform": plat, "name": name, "ok": False}
    try:
        qdir, session = _load_qoder()
    except AuthMissing as e:
        card.update(reason=e.code, report=e.report)
        card.update(e.extra)
        return card

    card["credential_file"] = qdir
    _begin_budget("web-status")
    try:
        res = signin.qoder_campaigns(session)
    except Exception as e:
        card.update(reason="ERROR",
                    report="查询失败（%s: %s）" % (type(e).__name__, e))
        return card

    if not res.get("ok"):
        reason = res.get("reason")
        if reason == "network":
            card.update(reason="NETWORK", report="网络不可达")
        elif reason == "auth":
            card.update(reason="NO_SESSION",
                        report="Qoder 登录态已失效（候选主机均返回 %s），"
                               "请在 Qoder 桌面端重新登录" % res.get("http"),
                        http=res.get("http"))
        else:
            card.update(reason="ERROR",
                        report="活动查询失败（HTTP %s）" % res.get("http"),
                        http=res.get("http"))
        return card

    campaigns = res.get("campaigns") or []
    picked = signin.qoder_pick_claimable(campaigns)
    card.update(
        ok=True,
        # Qoder 没有"今日签到"概念：无可领项即等价于"已处理完毕"，
        # 复用前端 checked_in 字段驱动"今日已签/待签到"的卡片形态
        checked_in=(len(picked) == 0),
        campaigns=len(campaigns),
        claimable_count=len(picked),
        credits=sum(int(signin._q_benefit_of(c).get("amount") or 0)
                    for c in picked),
        show_campaign=res.get("show_campaign"),
        claimable=res.get("claimable"),
    )
    if not campaigns:
        card["report"] = "当前无运营活动"
    elif not picked:
        card["report"] = "活动福利均已领取"
    return card


@app.get("/api/status")
def api_status():
    data = {"server_time": _now()}
    for p in PROVIDERS:
        data[p["id"]] = provider_status(p["id"])
    return ok(data)


# ============================================================
# 签到动作
# ============================================================
def _write_signin_log(entry):
    """把一次签到/查询结果追加进日志文件，保证网页签到也留下记录。

    与 signin.emit 同用 log_path() 且格式一致（[TIMESTAMP] {json}），网页端才
    能查得到。只追加、不抛错——日志坏了不能反过来让签到失败。
    """
    try:
        line = "[%s] %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                              json.dumps(entry, ensure_ascii=False, default=str))
        with open(log_path(), "a", encoding="utf-8") as lf:
            lf.write(line)
    except Exception:
        pass


def sign_workbuddy(plat="workbuddy", name="WorkBuddy"):
    """走 signin.run_daily —— 与 CLI 的 auto/silent 完全同一条路径。"""
    out = {"platform": plat, "name": name, "ok": False}
    try:
        _auth_file, _session, headers, endpoint = _load_wb()
    except AuthMissing as e:
        out.update(result=e.code, report=e.report)
        out.update(e.extra)
        _write_signin_log(out)
        return out

    _begin_budget("web-auto")
    try:
        code, result, _quiet = signin.run_daily(headers, endpoint)
    except Exception as e:
        out.update(result="ERROR",
                   report="执行异常（%s: %s）" % (type(e).__name__, e))
        _write_signin_log(out)
        return out

    out.update(ok=(code == 0),
               result=result.get("result") or "UNKNOWN",
               report=result.get("report") or "",
               exit_code=code,
               detail=result)
    _write_signin_log(out)
    return out


def sign_trae(plat="trae", name="Trae CN"):
    """Trae 侧：trae_status → 未签才 trae_claim。

    顺序与 _run_trae 的 auto 分支一致（先查后领，保证幂等），只是直接拿返回值
    而不是让 emit 去写 stdout/日志。
    """
    out = {"platform": plat, "name": name, "ok": False}
    try:
        _storage, _session, headers = _load_trae()
    except AuthMissing as e:
        out.update(result=e.code, report=e.report)
        out.update(e.extra)
        _write_signin_log(out)
        return out

    _begin_budget("web-auto")
    try:
        st = signin.trae_status(headers)
    except Exception as e:
        out.update(result="ERROR",
                   report="查询失败（%s: %s）" % (type(e).__name__, e))
        _write_signin_log(out)
        return out

    if "error" in st:
        code = st.get("error")
        if code == signin.CODE_NO_NETWORK:
            out.update(result="NETWORK", report="网络不可达，签到跳过")
        else:
            out.update(result="ERROR",
                       report="签到状态查询失败（HTTP %s）" % code, http=code)
        _write_signin_log(out)
        return out

    credits = st.get("credits")
    if st.get("checked_in"):
        out.update(ok=True, result="ALREADY", credits=credits,
                   report="今日已签（Trae CN，累计积分 %d）" % int(credits or 0))
        _write_signin_log(out)
        return out
    if not st.get("enable", True):
        out.update(ok=True, result="INACTIVE", credits=credits,
                   report="Trae CN 签到活动暂未开放")
        _write_signin_log(out)
        return out

    try:
        cl = signin.trae_claim(headers)
    except Exception as e:
        out.update(result="ERROR",
                   report="领取异常（%s: %s）" % (type(e).__name__, e))
        _write_signin_log(out)
        return out

    if cl.get("ok"):
        # claim 成功响应常常只有 {"code":0,"message":"success"}，不回报积分；
        # 用前面 status 拿到的 credits 兜底，卡片和日志里才有可读的数字。
        got = cl.get("credits") or credits or 0
        out.update(ok=True, result="CLAIMED", credits=got,
                   report="Trae CN 领取成功（累计积分 %d）" % int(got))
        _write_signin_log(out)
        return out

    out.update(result="ERROR",
               report="Trae CN 领取失败：%s" % (cl.get("message") or
                                              "HTTP %s" % cl.get("http")),
               detail=cl)
    _write_signin_log(out)
    return out


def sign_qoder(plat="qoder", name="Qoder"):
    """Qoder 侧：查活动 → 领取全部可领积分项（镜像 _run_qoder 的 auto 分支）。

    与 CLI 同语义：无活动=INACTIVE、全部已领=ALREADY、全成=CLAIM、
    部分=PARTIAL；一次领取失败即停，防废请求连环。
    """
    out = {"platform": plat, "name": name, "ok": False}
    try:
        _qdir, session = _load_qoder()
    except AuthMissing as e:
        out.update(result=e.code, report=e.report)
        out.update(e.extra)
        _write_signin_log(out)
        return out

    _begin_budget("web-auto")
    try:
        res = signin.qoder_campaigns(session)
    except Exception as e:
        out.update(result="ERROR",
                   report="查询失败（%s: %s）" % (type(e).__name__, e))
        _write_signin_log(out)
        return out

    if not res.get("ok"):
        reason = res.get("reason")
        if reason == "network":
            out.update(result="NETWORK", report="网络不可达，领取跳过")
        elif reason == "auth":
            out.update(result="NO_SESSION",
                       report="Qoder 登录态已失效（候选主机均返回 %s），"
                              "请在桌面端重新登录" % res.get("http"))
        else:
            out.update(result="ERROR",
                       report="活动查询失败（HTTP %s）" % res.get("http"),
                       http=res.get("http"))
        _write_signin_log(out)
        return out

    campaigns = res.get("campaigns") or []
    picked = signin.qoder_pick_claimable(campaigns)
    if not campaigns:
        out.update(ok=True, result="INACTIVE", report="Qoder 当前无运营活动")
        _write_signin_log(out)
        return out
    if not picked:
        out.update(ok=True, result="ALREADY",
                   report="Qoder 活动福利均已领取（共 %d 个活动）" % len(campaigns))
        _write_signin_log(out)
        return out

    got_total = 0
    got_count = 0
    fails = []
    for c in picked:
        amount = int(signin._q_benefit_of(c).get("amount") or 0)
        try:
            cl = signin.qoder_claim(session, c.get("campaignId"), amount)
        except Exception as e:
            fails.append({"campaignId": c.get("campaignId"),
                          "error": "%s: %s" % (type(e).__name__, e)})
            break
        if cl.get("ok"):
            got_count += 1
            got_total += amount
        else:
            fails.append({"campaignId": c.get("campaignId"),
                          "campaignKey": c.get("campaignKey"),
                          "http": cl.get("http"), "body": cl.get("body")})
        if fails or not signin._budget_left():
            break

    if not fails:
        out.update(ok=True, result="CLAIM", credits=got_total,
                   report="Qoder 领取成功（%d 项，积分 %d）" % (got_count, got_total))
    elif got_count:
        out.update(result="PARTIAL", credits=got_total,
                   report="Qoder 部分领取成功（%d 项 / 积分 %d，%d 项失败）" % (
                       got_count, got_total, len(fails)), failed=fails)
    elif any(f.get("http") == signin.CODE_NO_NETWORK for f in fails):
        out.update(result="NETWORK", report="网络不可达（领取失败）", failed=fails)
    else:
        out.update(result="ERROR", report="Qoder 领取失败", failed=fails)
    _write_signin_log(out)
    return out


@app.post("/api/signin/<platform>")
def api_signin(platform):
    if platform in ("both", "all"):
        # 三个引擎各签一次即可，避免同体系平台重复领取（幂等也会兜底，但没必要）
        targets = [("workbuddy", "wb"), ("trae", "trae"), ("qoder", "qoder")]
    elif platform in PROVIDER_MAP:
        targets = [(platform, PROVIDER_MAP[platform]["engine"])]
    else:
        return err("未知平台：%s（可用：%s / both）"
                   % (platform, " / ".join(PLATFORM_IDS)), "BAD_REQUEST")

    guarded = _guard()
    if guarded is not None:
        return guarded

    if not _OP_LOCK.acquire(blocking=False):
        return err("另一次签到正在执行，请等它结束后再试", "BUSY", 409)

    started = time.time()
    try:
        results = []
        for pid, engine in targets:
            prov = PROVIDER_MAP[pid]
            # both/all 走的是"每个真实引擎各签一次"，不在此拦截；这里只拦用户
            # 单独点某张卡片。未装客户端且被明确点名时，直接拒绝——否则同一份凭据
            # 会让"未安装"的 CodeBuddy 也显示签名成功（易误导，且若日后凭据分叉会误签）
            if (platform not in ("both", "all")
                    and not provider_readiness(prov)[0]):
                results.append({
                    "platform": pid, "name": prov["name"],
                    "ok": False, "result": "NO_AUTH",
                    "report": "未检测到 %s 客户端，请先安装并登录后再签到"
                              % prov["name"]})
                continue
            if engine == "wb":
                results.append(sign_workbuddy(pid, prov["name"]))
            elif engine == "trae":
                results.append(sign_trae(pid, prov["name"]))
            else:
                results.append(sign_qoder(pid, prov["name"]))
    finally:
        _OP_LOCK.release()

    return ok({
        "platform": platform,
        "started_at": datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": _now(),
        "elapsed": round(time.time() - started, 1),
        "results": results,
    }, ok_all=all(r.get("ok") for r in results))


@app.post("/api/growth")
def api_growth():
    """只跑成长中心（对应 CLI 的 growth 命令），补签之外想看成长任务时用。"""
    guarded = _guard()
    if guarded is not None:
        return guarded

    if not _OP_LOCK.acquire(blocking=False):
        return err("另一次操作正在执行，请稍后再试", "BUSY", 409)
    try:
        try:
            _auth_file, _session, headers, endpoint = _load_wb()
        except AuthMissing as e:
            return err(e.report, e.code, 400, **e.extra)

        _begin_budget("web-growth")
        try:
            code, result = signin.run_growth(headers, endpoint)
        except Exception as e:
            return err("执行异常（%s: %s）" % (type(e).__name__, e), "ERROR", 500)
    finally:
        _OP_LOCK.release()

    return ok({"platform": "workbuddy", "exit_code": code, **result},
              ok=(code == 0))


# ============================================================
# 日志
# ============================================================
def log_path():
    """与 signin.emit 完全一致的取值顺序，保证读的就是它写的那份。"""
    return (os.environ.get("WORKBUDDY_SIGNIN_LOG")
            or os.path.join(SIGNIN_DIR, "signin.log"))


def _read_log_lines():
    path = log_path()
    if not os.path.exists(path):
        return [], path
    try:
        size = os.path.getsize(path)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            if size > LOG_TAIL_BYTES:
                f.seek(size - LOG_TAIL_BYTES)
                f.readline()          # 丢弃被截断的半行
            return f.read().splitlines(), path
    except Exception:
        return [], path


def _parse_log_line(raw):
    m = LINE_RE.match(raw)
    if not m:
        return {"time": None, "platform": "workbuddy", "result": "INFO",
                "label": RESULT_LABELS["INFO"], "report": raw, "raw": raw}
    ts, body = m.group(1), m.group(2)
    try:
        payload = json.loads(body)
    except Exception:
        payload = {"report": body}
    if not isinstance(payload, dict):
        payload = {"report": str(payload)}

    platform = payload.get("platform")
    if platform not in ("workbuddy", "trae", "qoder"):
        # WorkBuddy 侧的日志不带 platform 字段，只能靠 step 前缀区分
        step = str(payload.get("step") or "")
        if step.startswith("trae"):
            platform = "trae"
        elif step.startswith("qoder"):
            platform = "qoder"
        else:
            platform = "workbuddy"

    result = payload.get("result") or "INFO"
    return {
        "time": ts,
        "platform": platform,
        "result": result,
        "label": RESULT_LABELS.get(result, result),
        "report": payload.get("report") or payload.get("step") or body,
        "step": payload.get("step"),
        "trigger": payload.get("trigger"),
        "poll": payload.get("trigger") == "poll",
        "credits": payload.get("credits") if payload.get("credits") is not None
        else payload.get("total_credits"),
        "streak_days": payload.get("streak_days"),
        "growth": payload.get("growth"),
        "growth_result": payload.get("growth_result"),
        "checked_in": payload.get("checked_in"),
        "http": payload.get("http"),
        "config_warning": payload.get("config_warning"),
        "raw": raw,
    }


def _log_facets(entries):
    by_platform = {"workbuddy": 0, "trae": 0, "qoder": 0}
    by_result = {}
    for e in entries:
        by_platform[e["platform"]] = by_platform.get(e["platform"], 0) + 1
        by_result[e["result"]] = by_result.get(e["result"], 0) + 1
    return by_platform, by_result


@app.get("/api/logs")
def api_logs():
    platform = (request.args.get("platform") or "all").lower()
    result = request.args.get("result") or "all"
    keyword = (request.args.get("q") or "").strip()
    poll_only = (request.args.get("poll") or "").lower() in ("1", "true", "yes")
    try:
        limit = int(request.args.get("limit") or LOG_DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = LOG_DEFAULT_LIMIT
    limit = max(1, min(limit, LOG_MAX_LIMIT))

    lines, path = _read_log_lines()
    # 逆序：最近的在最上面
    entries = [_parse_log_line(x) for x in reversed(lines)]
    by_platform, by_result = _log_facets(entries)

    def keep(e):
        if platform in ("workbuddy", "trae", "qoder") and e["platform"] != platform:
            return False
        if result != "all" and e["result"] != result:
            return False
        if poll_only and not e["poll"]:
            return False
        if keyword and keyword.lower() not in e["raw"].lower():
            return False
        return True

    filtered = [e for e in entries if keep(e)]
    return ok({
        "path": path,
        "exists": os.path.exists(path),
        "total": len(entries),
        "matched": len(filtered),
        "limit": limit,
        "facets": {"platform": by_platform, "result": by_result},
        "entries": filtered[:limit],
    })


@app.post("/api/logs/clear")
def api_logs_clear():
    """清空日志文件（保留路径，便于后续继续追加）。"""
    guarded = _guard()
    if guarded is not None:
        return guarded
    path = log_path()
    existed = os.path.exists(path)
    removed = 0
    if existed:
        try:
            lines, _ = _read_log_lines()
            removed = len(lines)
            with open(path, "w", encoding="utf-8") as f:
                f.truncate(0)
        except Exception as e:
            return err("清空日志失败（%s: %s）" % (type(e).__name__, e),
                       "LOG_ERROR", 500)
    return ok({
        "path": path,
        "removed": removed,
        "exists": os.path.exists(path),
        "message": "已清空日志，删除 %d 条记录" % removed,
    })


# ============================================================
# 计划任务
# ============================================================
_PS_BEGIN = "<<<WBJSON>>>"
_PS_END = "<<<WBJSONEND>>>"

# 一次性把三个任务的状态和下次运行时间取回来。Get-ScheduledTaskInfo 只认单个
# 任务名，所以这里按名字循环，而不是一次 -TaskName @(...) 批量查。
_PS_LIST = r"""
$ErrorActionPreference = 'SilentlyContinue'
$names = @('WorkBuddyAutoSignin','WorkBuddyGrowthPoll','TraeAutoSignin','QoderAutoSignin')
$out = @()
foreach ($n in $names) {
  $t = Get-ScheduledTask -TaskName $n
  if ($null -eq $t) {
    $out += [PSCustomObject]@{ name = $n; installed = $false; state = ''; next = ''; last = ''; lastResult = '' }
    continue
  }
  $next = ''; $last = ''; $res = ''
  $i = Get-ScheduledTaskInfo -TaskName $n
  if ($null -ne $i) {
    if ($null -ne $i.NextRunTime) { $next = $i.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss') }
    if ($null -ne $i.LastRunTime) { $last = $i.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss') }
    $res = [string]$i.LastTaskResult
  }
  $out += [PSCustomObject]@{ name = $n; installed = $true; state = [string]$t.State; next = $next; last = $last; lastResult = $res }
}
Write-Output '<<<WBJSON>>>'
Write-Output (ConvertTo-Json -InputObject @($out) -Depth 4 -Compress)
Write-Output '<<<WBJSONEND>>>'
"""


def _ansi_encoding():
    """系统 ANSI 代码页（简体中文 Windows 为 cp936），用于解码 schtasks 的原生消息。

    注意不能用 locale.getpreferredencoding()：本机实测 Python 开了 UTF-8 模式
    （sys.flags.utf8_mode == 1），该函数会返回 "UTF-8"，而 schtasks 写出来的
    其实是 GBK 字节，用它解码只会得到乱码。GetACP() 才是真正的系统代码页。
    """
    if os.name != "nt":
        return "utf-8"
    try:
        cp = ctypes.windll.kernel32.GetACP()
        if cp:
            return "cp%d" % cp
    except Exception:
        pass
    return "mbcs"


def _decode_ps(raw):
    """解码子进程输出。

    实测同一台机器上编码并不统一：schtasks.exe 的原生消息走系统 ANSI 代码页
    （GBK，例如「错误: 系统中不存在指定的任务名」），而 PowerShell 的 cmdlet
    有时吐 UTF-8、有时又吐 GBK，取决于调用方式与是否经过管道。

    所以顺序很关键：**先严格 utf-8，再落到 ANSI 代码页**。
      * 纯 ASCII 与真 UTF-8 都一步命中，不会被误伤；
      * GBK 中文的字节序列几乎不可能同时是合法 UTF-8，严格模式会失败，
        于是正确地落到 cp936 分支。
    反过来先试 cp936，就会把 UTF-8 的中文任务名解成乱码。
    """
    if not raw:
        return ""
    # PowerShell 5.1 管道重定向时会带上 UTF-8 BOM，先摘掉，免得干扰后面的判断
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            pass
    seen = []
    for enc in ("utf-8", _ansi_encoding(), "mbcs"):
        if enc and enc not in seen:
            seen.append(enc)
    for enc in seen:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def _run_powershell(script, timeout=25):
    """返回 (ok, stdout, stderr, returncode)。

    returncode 为子进程真实退出码，进程压根没跑起来时是 None —— 上层靠这个区分
    "命令执行了但失败"与"环境不可用"。非 Windows 上会干净地失败而不是抛栈。
    """
    if os.name != "nt":
        return False, "", "计划任务管理目前只实现了 Windows（schtasks / Get-ScheduledTask）", None
    exe = os.environ.get("COMSPEC_PS") or "powershell"
    args = [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-Command", script]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        # stdin 必须接空：schtasks /Change 会弹出交互式的 run-as 密码提示
        #（"请输入 xxx 的密码:"），继承来的 stdin 会让请求线程一直等下去。
        # 实测接 DEVNULL 后它读到 EOF 就继续，改动照样生效。
        proc = subprocess.run(args, capture_output=True, timeout=timeout,
                              creationflags=flags, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return False, "", "未找到 powershell，可用命令行改用 install-windows.ps1 管理任务", None
    except subprocess.TimeoutExpired:
        return False, "", "PowerShell 执行超时（%ss）" % timeout, None
    except Exception as e:
        return False, "", "调用 PowerShell 失败（%s: %s）" % (type(e).__name__, e), None
    return (proc.returncode == 0,
            _decode_ps(proc.stdout),
            _decode_ps(proc.stderr).strip(),
            proc.returncode)


def _extract_ps_json(out):
    s = out.find(_PS_BEGIN)
    e = out.find(_PS_END)
    if s < 0 or e < 0 or e <= s:
        return None
    raw = out[s + len(_PS_BEGIN):e].strip().lstrip("\ufeff").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _tasks_payload():
    """返回 (任务列表, 警告信息或 None)。"""
    meta = {t["name"]: t for t in TASKS}
    status_ok, out, serr, _rc = _run_powershell(_PS_LIST)
    data = _extract_ps_json(out) if status_ok else None

    if isinstance(data, dict):       # 单元素时 ConvertTo-Json 可能退化成对象
        data = [data]

    if not isinstance(data, list):
        installed = {t: {"name": t, "installed": False} for t in TASK_NAMES}
        warning = serr or "未能解析计划任务列表（可在命令行用 schtasks /query 手动确认）"
    else:
        installed = {}
        for row in data:
            if isinstance(row, dict) and row.get("name"):
                installed[row["name"]] = row
        warning = None

    items = []
    for name in TASK_NAMES:
        row = installed.get(name) or {"name": name, "installed": False}
        item = dict(meta.get(name) or {})
        item.update({
            "name": name,
            "installed": bool(row.get("installed")),
            "state": row.get("state") or "",
            "next_run": row.get("next") or None,
            "last_run": row.get("last") or None,
            "last_result": row.get("lastResult") or None,
            "can_change_time": (meta.get(name) or {}).get("kind") == "signin",
        })
        items.append(item)

    return items, warning


@app.get("/api/tasks")
def api_tasks():
    items, warning = _tasks_payload()
    any_installed = any(i["installed"] for i in items)
    return ok({
        "available": os.name == "nt",
        "any_installed": any_installed,
        "items": items,
        "warning": warning,
        "install_command": None if any_installed else
        (r".\install-windows.ps1 -Platform both -Time 00:05"),
        "uninstall_hint": "schtasks /Delete /TN <任务名> /F",
    })


def _task_meta(name):
    for t in TASKS:
        if t["name"] == name:
            return t
    return None


def _task_registered(name):
    """任务是否已在本机注册：True / False，环境不可用时 None。

    只看退出码，不碰任何报错文本。这是被实测逼出来的结论：同一台机器上
    `schtasks /Query` 对不存在的任务回的是英文 "ERROR: The system cannot find
    the file specified."，而 `/Change` 回的是中文 GBK「错误: 系统中不存在指定的任务名」。
    文案都这样自相矛盾，靠关键词（"找不到"/"does not exist"）判断必然会漏。
    退出码则与系统语言、输出编码完全无关。
    """
    _ok, _out, _serr, code = _run_powershell(
        "& schtasks.exe /Query /TN '%s' > $null 2>&1; exit $LASTEXITCODE" % name,
        timeout=15)
    if code is None:
        return None
    return code == 0


def _clean_ps_error(text):
    """从 PowerShell 报错里挑出真正有用的那一行。

    schtasks 的失败文本会混进 "所在位置 行:1 字符: 1"、"& schtasks.exe ..."、
    "CategoryInfo"、"FullyQualifiedErrorId" 这些定位噪声，原样丢给用户没法看。
    """
    kept = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if line.startswith(("+", "~")):
            continue
        if low.startswith(("categoryinfo", "fullyqualifiederrorid")):
            continue
        if line.startswith("所在位置") or low.startswith("at line"):
            continue
        kept.append(line)
    if not kept:
        return (text or "").strip()
    head = kept[0]
    # schtasks 原生报错形如 "schtasks.exe : 错误: ..."，剥掉命令前缀
    prefix, sep, tail = head.partition(" : ")
    if sep and ("schtasks" in prefix.lower() or prefix.lower().endswith(".exe")):
        head = tail.strip()
    return head


def _schtasks_change(name, flag, extra_ok_msg):
    """改任务参数（启用 / 禁用 / 改时间）。

    失败判定全部基于退出码。实测（见项目自测脚本）：改时间、禁用、启用成功一律
    返回 0；时间非法、任务不存在一律返回非 0。所以没必要解析报错文本 —— 之前
    正是因为解析文本 + 乱码，才把"任务未安装"这种可预期的用户错误报成了 500。
    """
    exists = _task_registered(name)
    if exists is None:
        return err("无法调用 PowerShell 查询计划任务，请在命令行确认 schtasks 是否可用",
                   "TASK_ERROR", 500)
    if not exists:
        return err("任务 %s 尚未在本机注册，请先运行 install-windows.ps1 安装后再试"
                   % name, "TASK_MISSING", 400)

    _ok, out, serr, code = _run_powershell(
        "& schtasks.exe /Change /TN '%s' %s 2>&1 | Out-String; exit $LASTEXITCODE"
        % (name, flag),
        timeout=20)
    if code is None:
        return err(serr or "调用 PowerShell 失败", "TASK_ERROR", 500)
    if code != 0:
        # 退出码已经说明失败了，文本只用来给用户一个具体原因；捞不到就报通用话术
        detail = _clean_ps_error(out) or serr or "schtasks 执行失败（退出码 %s）" % code
        return err(detail, "TASK_ERROR", 500)
    # 成功时不回传 schtasks 的原始输出：里面混着"请输入 xxx 的密码"这类
    # 交互提示噪声（即使改动已经生效），显示在网页上只会让人以为出了问题。
    return ok({"name": name, "message": extra_ok_msg})


@app.post("/api/tasks/<name>/toggle")
def api_task_toggle(name):
    meta = _task_meta(name)
    if not meta:
        return err("未知任务：%s（可用：%s）" % (name, " / ".join(TASK_NAMES)), "BAD_REQUEST")

    guarded = _guard()
    if guarded is not None:
        return guarded

    payload = request.get_json(silent=True) or {}
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        return err("请求体需要 {\"enabled\": true/false}", "BAD_REQUEST")

    return _schtasks_change(name, "/ENABLE" if enabled else "/DISABLE",
                            "已启用" if enabled else "已禁用")


@app.post("/api/tasks/<name>/time")
def api_task_time(name):
    meta = _task_meta(name)
    if not meta:
        return err("未知任务：%s（可用：%s）" % (name, " / ".join(TASK_NAMES)), "BAD_REQUEST")

    guarded = _guard()
    if guarded is not None:
        return guarded

    if meta["kind"] != "signin":
        return err(
            "「%s」一天要跑六轮（01/05/09/13/17/21 点），改单个时间没有意义，"
            "所以这里不提供修改。要调整请重新运行 install-windows.ps1。" % meta["label"],
            "UNSUPPORTED", 400)

    payload = request.get_json(silent=True) or {}
    when = str(payload.get("time") or "").strip()
    if not TIME_RE.match(when):
        return err("时间格式应为 24 小时制 HH:MM（例如 00:05）", "BAD_REQUEST")

    return _schtasks_change(name, "/ST %s" % when, "已改为 %s" % when)


# ============================================================
# 自动签到（config.json + 后台调度线程）
# ============================================================
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DEFAULT_AUTO_TIME = "09:00"
TIME_RE_IS = TIME_RE  # 复用上面的 HH:MM 白名单

# 进程级内存缓存；写文件前同步内存，线程内只读内存
_cfg = {"enabled": False, "time": DEFAULT_AUTO_TIME, "last_run_date": None,
        "scheduled": False, "auto_exit": True}
_cfg_lock = threading.Lock()

# "由系统定时任务拉起签到" 相关参数
SCHED_TASK_NAME = "AutoSigninLaunch"      # 计划任务名
SCHED_LEAD_MINUTES = 1                    # 在自动签到时间前提前多少分钟拉起
DEFAULT_EXIT_SECONDS = 300                # 签到后多少秒自动退出（默认 5 分钟）


def _load_config():
    global _cfg
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg = {"enabled": False, "time": DEFAULT_AUTO_TIME, "last_run_date": None,
                   "scheduled": False, "auto_exit": True}
            cfg["enabled"] = bool(data.get("enabled"))
            cfg["time"] = str(data.get("time") or DEFAULT_AUTO_TIME)
            if not TIME_RE_IS.match(cfg["time"]):
                cfg["time"] = DEFAULT_AUTO_TIME
            cfg["last_run_date"] = data.get("last_run_date")
            cfg["scheduled"] = bool(data.get("scheduled"))
            cfg["auto_exit"] = bool(data.get("auto_exit", True))
            _cfg = cfg
    except Exception:
        pass


def _save_config():
    with _cfg_lock:
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(_cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def _cfg_snapshot():
    with _cfg_lock:
        return dict(_cfg)


def _set_auto(enabled, when):
    with _cfg_lock:
        _cfg["enabled"] = bool(enabled)
        if when is not None:
            _cfg["time"] = when
    _save_config()


def _on_auto_signin_run():
    """后台线程在到点时执行一次完整自动签到（含就绪判定）。"""
    if not _OP_LOCK.acquire(blocking=False):
        return  # 一次性跑，别再叠加
    try:
        results = []
        try:
            signin._sleep_jitter()
        except Exception:
            pass
        for pid in PLATFORM_IDS:
            prov = PROVIDER_MAP[pid]
            installed, _found, _looked = provider_readiness(prov)
            if not installed:
                # 客户端都没装 → 判“未检测到”，跳过（有凭据没装同样算，因为口径是两者都要）
                results.append({
                    "platform": pid, "name": prov["name"], "ok": False,
                    "result": "NO_AUTH",
                    "report": "未检测到 %s 客户端（疑似未安装），自动签到跳过" % prov["name"],
                })
                continue
            if prov["engine"] == "wb":
                results.append(sign_workbuddy(pid, prov["name"]))
            elif prov["engine"] == "trae":
                results.append(sign_trae(pid, prov["name"]))
            else:
                results.append(sign_qoder(pid, prov["name"]))
        return results
    finally:
        _OP_LOCK.release()


def plat_has_credential(pid):
    """某个平台是否已有可用凭据（对同引擎平台复用同一凭据存在性判定）。"""
    engine = PROVIDER_MAP[pid]["engine"]
    loader = {"wb": _load_wb, "trae": _load_trae, "qoder": _load_qoder}[engine]
    try:
        loader()
        return True
    except AuthMissing:
        return False


def _auto_scheduler():
    """常驻判断：到点时触发一次自动签到，一天只触发一次。"""
    while True:
        time.sleep(20)
        cfg = _cfg_snapshot()
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if cfg["enabled"] and cfg.get("last_run_date") != today:
            hhmm = cfg.get("time") or DEFAULT_AUTO_TIME
            if now.strftime("%H:%M") == hhmm:
                with _cfg_lock:
                    _cfg["last_run_date"] = today
                _save_config()
                try:
                    _on_auto_signin_run()
                except Exception:
                    pass


@app.get("/api/autosignin")
def api_autosignin():
    cfg = _cfg_snapshot()
    per = {}
    for pid in PLATFORM_IDS:
        prov = PROVIDER_MAP[pid]
        installed, found, _looked = provider_readiness(prov)
        per[pid] = {
            "name": prov["name"], "sub": prov["sub"], "engine": prov["engine"],
            "installed": installed,
            "installed_path": found,
            "credential": plat_has_credential(pid),
            "ready": installed and plat_has_credential(pid),
        }
    return ok({
        "enabled": cfg["enabled"],
        "time": cfg["time"],
        "last_run_date": cfg.get("last_run_date"),
        "scheduled": cfg.get("scheduled", False),
        "auto_exit": cfg.get("auto_exit", True),
        "lead_minutes": SCHED_LEAD_MINUTES,
        "exit_seconds": DEFAULT_EXIT_SECONDS,
        "scheduled_task": scheduled_task_status(),
        "config_path": CONFIG_PATH,
        "autostart": autostart_status(),
        "platforms": per,
    })


@app.post("/api/autosignin")
def api_autosignin_update():
    guarded = _guard()
    if guarded is not None:
        return guarded
    payload = request.get_json(silent=True) or {}
    enabled = payload.get("enabled")
    when = payload.get("time")
    scheduled = payload.get("scheduled")
    auto_exit = payload.get("auto_exit")
    if enabled is not None and not isinstance(enabled, bool):
        return err("enabled 应为布尔值", "BAD_REQUEST")
    if when is not None and not TIME_RE_IS.match(str(when).strip()):
        return err("时间格式应为 24 小时制 HH:MM（例如 09:00）", "BAD_REQUEST")
    if scheduled is not None and not isinstance(scheduled, bool):
        return err("scheduled 应为布尔值", "BAD_REQUEST")
    if auto_exit is not None and not isinstance(auto_exit, bool):
        return err("auto_exit 应为布尔值", "BAD_REQUEST")

    cur = _cfg_snapshot()
    new_enabled = enabled if enabled is not None else cur["enabled"]
    new_time = str(when).strip() if when is not None else cur["time"]
    new_scheduled = scheduled if scheduled is not None else cur["scheduled"]
    new_auto_exit = auto_exit if auto_exit is not None else cur["auto_exit"]

    # 先同步计划任务（成功才落盘），避免配置与系统任务不一致
    sched_changed = (new_scheduled != cur["scheduled"]) or (when is not None and new_scheduled)
    if sched_changed:
        r = _sync_scheduled_task(new_scheduled, new_time)
        if r.get("ok") is False:
            return err(r.get("report", "同步计划任务失败"),
                       r.get("reason", "TASK_ERROR"),
                       r.get("status", 500))

    with _cfg_lock:
        _cfg["enabled"] = new_enabled
        _cfg["time"] = new_time
        _cfg["scheduled"] = new_scheduled
        _cfg["auto_exit"] = new_auto_exit
    _save_config()

    return ok({**(_cfg_snapshot()), "scheduled_task": scheduled_task_status(),
               "message": "设置已保存"})


# ============================================================
# 开机自启（注册表 Run 键）
# ============================================================
AUTOSTART_NAME = "WorkBuddyTraeAutoSignin"
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _autostart_command():
    """返回要写入 Run 键的命令（pythonw 拉起，无控制台窗口、不自动开浏览器）。"""
    py = sys.executable
    base = os.path.dirname(py)
    pyw = os.path.join(base, "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = py
    script = os.path.join(BASE_DIR, "webapp.py")
    return '"%s" "%s" --no-browser' % (pyw, script)


def autostart_status():
    if winreg is None or os.name != "nt":
        return {"available": False, "enabled": False, "command": None}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0,
                            winreg.KEY_READ) as k:
            val, _ = winreg.QueryValueEx(k, AUTOSTART_NAME)
        return {"available": True, "enabled": True, "command": val}
    except OSError:
        return {"available": True, "enabled": False, "command": None}


@app.post("/api/autostart")
def api_autostart():
    guarded = _guard()
    if guarded is not None:
        return guarded
    if winreg is None or os.name != "nt":
        return err("当前系统不支持注册表自启", "UNSUPPORTED", 400)
    payload = request.get_json(silent=True) or {}
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        return err("请求体需要 {\"enabled\": true/false}", "BAD_REQUEST")
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as k:
            if enabled:
                winreg.SetValueEx(k, AUTOSTART_NAME, 0, winreg.REG_SZ,
                                  _autostart_command())
            else:
                try:
                    winreg.DeleteValue(k, AUTOSTART_NAME)
                except OSError:
                    pass
    except Exception as e:
        return err("修改注册表失败（%s: %s）" % (type(e).__name__, e), "ERROR", 500)
    return ok({"message": "已开启开机自启" if enabled else "已关闭开机自启",
               **autostart_status()})


# ============================================================
# 由系统定时任务拉起签到（AutoSigninLaunch）
# 平时不常驻：系统任务在签到时间前 SCHED_LEAD_MINUTES 分钟拉起
# 「--scheduled」运行形态，签到完按 auto_exit 自动退出；失败则打开浏览器看日志。
# ============================================================

def _sched_launch_target():
    """返回 (pythonw 路径, webapp 脚本路径)，作为计划任务拉起的命令组合。"""
    py = sys.executable
    pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = py
    return pyw, os.path.join(BASE_DIR, "webapp.py")


def _lead_time(hm):
    """把 HH:MM 往前拨 SCHED_LEAD_MINUTES 分钟（跨天回绕）。"""
    hh, mm = (int(x) for x in str(hm).split(":"))
    total = (hh * 60 + mm - SCHED_LEAD_MINUTES) % 1440
    return "%02d:%02d" % (total // 60, total % 60)


def scheduled_task_status():
    if os.name != "nt":
        return {"available": False, "enabled": False, "trigger": None,
                "command": None}
    registered = _task_registered(SCHED_TASK_NAME)
    if registered is None:
        return {"available": True, "enabled": False, "trigger": None,
                "command": None, "error": "无法查询计划任务"}
    if not registered:
        return {"available": True, "enabled": False, "trigger": None,
                "command": None}
    return {"available": True, "enabled": True,
            "trigger": _lead_time(_cfg_snapshot().get("time") or DEFAULT_AUTO_TIME),
            "command": _sched_launch_target()[1]}


def _scheduled_task_exists():
    r = _task_registered(SCHED_TASK_NAME)
    return False if r is None else r


def _sched_install(when_hm):
    """注册/更新 AutoSigninLaunch：每日 when_hm 前 LEAD 分钟拉起签到。"""
    pyw, script = _sched_launch_target()
    at = _lead_time(when_hm)
    ps = (
        "$ErrorActionPreference='Stop';\n"
        "$pyw='%s';\n"
        "$script='%s';\n"
        "$at='%s';\n"
        "$arg='\"{0}\" --no-browser --scheduled' -f $script;\n"
        "$A=New-ScheduledTaskAction -Execute $pyw -Argument $arg;\n"
        "$T=New-ScheduledTaskTrigger -Daily -At $at;\n"
        "Register-ScheduledTask -TaskName '%s' -Action $A -Trigger $T "
        "-Force 2>&1 | Out-Null;\n"
        "exit 0\n"
    ) % (pyw.replace("'", "''"), script.replace("'", "''"),
         at, SCHED_TASK_NAME)
    _ok, _out, serr, code = _run_powershell(ps, timeout=30)
    if code != 0:
        return {"ok": False,
                "reason": "TASK_ERROR",
                "report": serr or _clean_ps_error(_out) or "注册计划任务失败",
                "status": 500}
    return {"ok": True,
            "message": "已注册计划任务，将在每日签到前 %d 分钟（%s）由系统拉起签到"
                       % (SCHED_LEAD_MINUTES, at),
            **scheduled_task_status()}


def _sched_uninstall():
    if not _scheduled_task_exists():
        return {"ok": True,
                "message": "计划任务未注册（无需注销）",
                **scheduled_task_status()}
    ps = ("$ErrorActionPreference='Stop';\n"
          "Unregister-ScheduledTask -TaskName '%s' -Confirm:$false 2>&1 | Out-Null;\n"
          "exit 0\n" % SCHED_TASK_NAME)
    _ok, _out, serr, code = _run_powershell(ps, timeout=30)
    if code != 0:
        return {"ok": False,
                "reason": "TASK_ERROR",
                "report": serr or _clean_ps_error(_out) or "注销计划任务失败",
                "status": 500}
    return {"ok": True,
            "message": "已注销计划任务，恢复为常驻/手动方式",
            **scheduled_task_status()}


def _sync_scheduled_task(enabled, when_hm):
    """按开关同步计划任务：启用 → 注册/更新时间；停用 → 注销。返回纯 dict。"""
    if enabled:
        return _sched_install(when_hm)
    return _sched_uninstall()


# ============================================================
# 页面与兜底
# ============================================================
@app.get("/")
def index():
    if not os.path.exists(os.path.join(WEB_DIR, "index.html")):
        return err("缺少 web/index.html，请确认项目文件完整", "NOT_FOUND", 404)
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/favicon.ico")
def favicon():
    name = "favicon.ico"
    if os.path.exists(os.path.join(WEB_DIR, name)):
        return send_from_directory(WEB_DIR, name)
    return "", 204


@app.get("/api/health")
def api_health():
    return ok({
        "service": "workbuddy-trae-auto-signin web console",
        "time": _now(),
        "signin_py": os.path.abspath(signin.__file__),
        "log_path": log_path(),
        "log_exists": os.path.exists(log_path()),
        "platform": sys.platform,
        "python": sys.version.split()[0],
    })


@app.errorhandler(Exception)
def handle_any(e):
    """任何漏网异常都翻译成 JSON——目标之一是永远不给浏览器 500 白屏。"""
    if isinstance(e, HTTPException):
        return err(e.description or e.name, "HTTP_%s" % e.code, e.code)
    import traceback
    return err("服务端异常（%s: %s）" % (type(e).__name__, e), "SERVER_ERROR", 500,
               trace=traceback.format_exc().splitlines()[-1])


# ============================================================
# 入口
# ============================================================
def _open_browser_later(url, delay=1.2):
    def worker():
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Thread(target=worker, daemon=True).start()


def _once_scheduled(port):
    """计划任务拉起的签到模式：启动即签一次；按结果自动退出；失败弹浏览器看日志。

    是否"失败"只认真实报错（ERROR/NETWORK/TIMEOUT）——平台未安装（NO_AUTH）、
    已签到（ALREADY）都不算失败，避免没装 CodeBuddy 就每次都弹浏览器。
    """
    cfg = _cfg_snapshot()
    results = []
    try:
        results = _on_auto_signin_run() or []
    except Exception as e:
        results.append({"ok": False, "result": "ERROR",
                        "report": "签到异常（%s: %s）" % (type(e).__name__, e)})
    failed = any((r.get("result") or "") in ("ERROR", "NETWORK", "TIMEOUT")
                 for r in results)
    if failed:
        _open_browser_later("http://127.0.0.1:%d/?open=log" % port, delay=1.0)
    secs = DEFAULT_EXIT_SECONDS if cfg.get("auto_exit", True) else None
    if secs:
        def _bye():
            time.sleep(secs)
            try:
                os._exit(0)
            except Exception:
                pass
        threading.Thread(target=_bye, daemon=True).start()


def main():
    ap = argparse.ArgumentParser(
        description="WorkBuddy + Trae CN 签到脚本的本地 Web 控制台")
    ap.add_argument("--host", default="127.0.0.1",
                    help="监听地址，默认 127.0.0.1（仅本机）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help="监听端口，默认 %d" % DEFAULT_PORT)
    ap.add_argument("--allow-remote", action="store_true",
                    help="允许绑定到非回环地址（默认拒绝，避免把签到接口暴露到局域网）")
    ap.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    ap.add_argument("--scheduled", action="store_true",
                    help="由系统计划任务拉起的签到模式：启动即签到一次，"
                         "签完按 auto_exit 自动退出；失败自动打开浏览器看日志")
    args = ap.parse_args()

    if args.host not in LOOPBACK_HOSTS and not args.allow_remote:
        sys.stderr.write(
            "--host %s 不是回环地址。控制台能改动本机计划任务并触发签到，"
            "默认不允许对外暴露；确需如此请加 --allow-remote。\n" % args.host)
        return 2

    url = "http://%s:%d/" % ("127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host,
                             args.port)
    print("=" * 62)
    print(" WorkBuddy + Trae CN 签到控制台")
    print("=" * 62)
    print(" 地址   : %s" % url)
    print(" 日志   : %s" % log_path())
    print(" 脚本   : %s" % os.path.abspath(signin.__file__))
    print(" 停止   : Ctrl+C")
    print("=" * 62)

    if not args.no_browser:
        _open_browser_later(url)
    # debug/reloader 必须关：热重载会 fork 出第二个进程，签到锁就形同虚设
    _load_config()
    if args.scheduled:
        # 计划任务拉起：常驻签到线程不再启动，由本进程一次性签到后自动退出
        _once_scheduled(args.port)
    else:
        threading.Thread(target=_auto_scheduler, daemon=True).start()
    app.run(host=args.host, port=args.port, debug=False,
            threaded=True, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())