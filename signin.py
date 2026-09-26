"""WorkBuddy + Trae CN + Qoder 每日签到自动领取脚本（单文件）。

能力：
  - WorkBuddy / CodeBuddy 每日签到 + 成长中心
  - Trae CN 每日签到（支持 Trae Solo CN / Trae CN / Trae 三条产品线）
  - Qoder 活动福利领取（Qoder 无每日签到，等价动作=领取全部可领积分活动）
  - 纯 Python 标准库实现，零第三方依赖
  - 内置 AES-128-CBC 实现（优先用系统 openssl，缺失或失败时回落到内嵌纯 Python 版）

协议：MIT

说明：接口与解密算法系从桌面端逆向分析所得，仅供学习与研究使用；
服务端改一版就可能失效，封号等使用后果由使用者自行承担。

WorkBuddy 接口契约：
  POST {endpoint}/v2/billing/meter/checkin-activity-status  查询签到状态
  POST {endpoint}/v2/billing/meter/daily-checkin            领取今日积分

  响应：
    - 领取成功 : {"credit": 100}
    - 今日已签 : null 或 HTTP 400 + {"code":10001,"msg":"今天已签到，请明天再来"}
                 幂等，两种形态都按"已签"处理，不计失败
    - 登录失效 : HTTP 401/403，需重新登录桌面端

Trae CN 接口契约：
  POST https://api.trae.cn/trae/api/v2/ug/checkin_credits/status  查签到状态
  POST https://api.trae.cn/trae/api/v2/ug/checkin_credits/claim   领取积分

  两个接口都必须带请求体 {"req_source": 1}（SOLO Lite 为 2），空 body 会被
  服务端判为参数异常。

  认证方式：读本机 TRAE 桌面端 storage.json 中的
    iCubeAuthInfo://icube.cloudide 键（base64 密文，AES-128-CBC 加密，
    密钥由内嵌 SHA-512 KDF 从密文头部 32 字节派生），解密后拿 token
    塞进 "Authorization: Cloud-IDE-JWT <token>"。

  设备指纹（对齐官方客户端 fb()，缺项就不发）：
    x-device-id     ← storage.json 里 "iCubeAuthInfo://icube-dc:<did>" 的键后缀
                      （native ahaDeviceService 生成，非 telemetry.devDeviceId）
    x-device-brand  ← device_model，URL 编码
    x-device-type   ← os_name
    x-os-version    ← os_version，URL 编码
    x-app-version   ← 安装目录 resources/app/product.json 的 appVersion
    x-machine-id    ← telemetry.machineId
  device_model / os_version 从客户端 renderer.log 的 common_params 取原值，
  日志被清理时省略该头。设备指纹不匹配会返回 9074「当前参与用户太多」。

  响应：
    - status: {"checked_in": bool, "enable": bool, "credits": int,
               "extra_credits": int, "code": 0}
    - claim : {"code": 0, "message": "success"}（成功时通常不回报积分）

Qoder 接口契约：
  GET  https://openapi.qoder.sh/sash/api/v1/me/campaigns        查询运营活动
  POST https://openapi.qoder.sh/sash/api/v1/me/campaigns/{id}/claim  领取福利
  POST https://openapi.qoder.sh/api/v1/deviceToken/refresh      刷新令牌（备用）

  国际版（qoder.sh）与国内版（openapi.qoder.com.cn）是两套独立账号系统，
  按序探测候选主机、先应答者留下；两套主机均 401/403 才判登录失效。
  404 按官方前端行为视为"无活动"，不算错误。

  认证方式：读本机 Qoder 桌面端数据目录（auth.v1.dat + Local State 同在）。
    Local State 的 os_crypt.encrypted_key（base64，去 "DPAPI" 前缀）经
    Windows DPAPI（CryptUnprotectData）解出 32 字节 AES key；
    auth.v1.dat 布局 b"v10" + nonce(12) + ciphertext + GCM tag(16)，
    用内嵌纯 Python AES-256-GCM 解密得 {token, refreshToken, expiresAt, user}。
    因此 Qoder 段仅限 Windows 桌面端登录后的本机运行。

  活动响应：{"showCampaign": bool, "claimable": bool,
            "campaigns": [{campaignId, campaignKey, actionType,
                           claimStatus: CLAIMABLE|CLAIMED,
                           benefit: {kind: CREDITS, amount}}]}
  仅 actionType=CLAIM_BENEFIT 且 claimStatus=CLAIMABLE 且 kind=CREDITS 的
  活动会领取；无活动 INACTIVE、全部已领 ALREADY。

用法：
  # WorkBuddy
  python signin.py auto           # 每日自动化：签到 + 成长中心
  python signin.py silent         # 同 auto，静默模式（写日志文件）
  python signin.py growth         # 仅成长中心
  python signin.py silent-poll    # 补签 + 成长中心（未签才签，空跑不写日志）
  python signin.py silent-growth  # silent-poll 的旧名
  python signin.py status         # 仅查签到状态
  python signin.py claim          # 仅领取签到
  python signin.py all            # 查状态 + 领取

  # Trae CN
  python signin.py trae           # Trae CN 完整自动签到
  python signin.py trae silent    # Trae CN 静默模式
  python signin.py trae status    # Trae CN 查状态
  python signin.py trae claim     # Trae CN 强制签到

  # Qoder
  python signin.py qoder          # Qoder 领取全部可领活动
  python signin.py qoder silent   # Qoder 静默模式
  python signin.py qoder status   # Qoder 查活动/可领状态
  python signin.py qoder claim    # Qoder 领取（同 qoder，服务端幂等）

  # 全平台一起跑
  python signin.py both           # WB 完整 + Trae 完整 + Qoder 完整
  python signin.py both silent    # 三平台静默签到

环境变量：
  WB_AUTH_FILE / WORKBUDDY_AUTH_FILE    — WorkBuddy auth.json 路径
  WB_BUDGET_SECONDS / WORKBUDDY_BUDGET_SECONDS — 时间预算
  WB_SIGNIN_LOG / WORKBUDDY_SIGNIN_LOG  — 签到日志文件
  WB_GROWTH_LOG_EMPTY                   — 是否记录 no_op 成长中心日志
  TRAE_AUTH_FILE                        — Trae storage.json 路径（; 分隔多个）
  TRAE_STORAGE_DIR                      — Trae 存储根目录（覆盖自动探测）
  QODER_AUTH_FILE                       — Qoder auth.v1.dat 完整路径（覆盖自动探测）
  QODER_STORAGE_DIR                     — Qoder 数据目录（须同时含 auth.v1.dat 与 Local State）
  QODER_API_BASE                        — Qoder API 主机覆盖（如 https://openapi.qoder.sh）
  OPENSSL_BIN                           — openssl 可执行文件完整路径（可选；
                                          未设置或无效时自动走内嵌纯 Python AES）

任何模式下都不会打印令牌，可安全分享日志。
"""

import base64
import glob
import hashlib
import json
import math
import os
import random
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime

DEFAULT_ENDPOINT = "https://copilot.tencent.com"
AUTH_BASENAME = os.path.join("CodeBuddyExtension", "Data", "Public", "auth", "workbuddy-desktop.info")
# Linux 没有桌面端，入口是 CodeBuddy CLI；它写出的凭据文件名不同、放在 XDG 数据目录
# （issue #4 实测：~/.local/share/CodeBuddyExtension/Data/Public/auth/Tencent-Cloud.coding-copilot.info，
#  JSON 结构与桌面端一致，签到/成长中心接口全部照常工作）
CLI_AUTH_BASENAME = os.path.join("CodeBuddyExtension", "Data", "Public", "auth",
                                 "Tencent-Cloud.coding-copilot.info")

# ============================================================
# Trae CN 相关常量（对应社区签到包 checkin.js 的常量）
# ============================================================
# 存储键名：iCubeAuthInfo://icube.cloudide
TRAE_AUTH_KEY = "iCubeAuthInfo://icube.cloudide"

# 设备密钥对存储键前缀：iCubeAuthInfo://icube-dc:<AHA deviceId>
# 后缀那串数字就是官方客户端签到时上报的 x-device-id（native ahaDeviceService
# 生成，客户端日志里写作 "[ICDRS] (init) ... did: 4162079293974057"）。
# 注意：telemetry.devDeviceId 是 VS Code 侧的机器码，跟它不是一个东西，
# 用错了会被签到接口判为异常请求（表现为 9074「当前参与用户太多」）。
TRAE_DC_KEY_PREFIX = "iCubeAuthInfo://icube-dc:"

# 存储路径（三产品线自动探测，任一命中即可）
# 顺序：优先 CN 版（更常见），最后兜底国际版
TRAE_WIN_APPDATA_DIRS = (
    "TRAE SOLO CN",   # Trae Solo CN（社区包唯一确认的目录）
    "TRAE CN",        # Trae CN（合并版推测，需按实际存在性命中）
    "Trae CN",        # 变体
    "TRAE",           # 国际版兜底
    "Trae",           # 国际版兜底
)
TRAE_STORAGE_SUBDIR = os.path.join("User", "globalStorage", "storage.json")
TRAE_MACOS_DIRS = ("TRAE SOLO CN", "TRAE CN", "Trae CN", "Trae")

# API 端点
TRAE_API_BASE = "https://api.trae.cn"
TRAE_STATUS_URL = TRAE_API_BASE + "/trae/api/v2/ug/checkin_credits/status"
TRAE_CLAIM_URL  = TRAE_API_BASE + "/trae/api/v2/ug/checkin_credits/claim"

# claim 遇「根本没拿到响应」（连不上/超时）时的重试次数。业务错误码不重试，
# 避免重复打 claim 接口真的把账号打进风控。
TRAE_CLAIM_RETRIES = 3

# 官方客户端 POST checkin 接口时固定带 body {"req_source": n}：
# n=1 为普通 Trae，n=2 为 SOLO Lite（客户端内部用 gr() 判定产品线）。
# 空 body 会被服务端判为参数异常，这是原先签到失败的直接原因之一。
TRAE_REQ_SOURCE = 1

# 加密常量（源自 checkin.js）
# 密文布局：[key_material(32) | iv_material(16) | ciphertext]
# 但实际 base64 解码后，前 Em=6 字节是"盐/版本头"，再后面 32 字节是 key，再后面 64 字节是 iv+sha 混合，再后面是密文
# 实际布局（对齐 JS）：
#   offset 0..Em-1        : 忽略（6 字节）
#   offset Em..Em+Rv-1    : key_material (32 字节，用于 SHA-512 派生)
#   offset Em+Rv..end     : 密文
_TR_HP = 16      # AES-128 块大小
_TR_Q8 = 16      # AES key 长度
_TR_WP = 16      # IV 长度
_TR_RH = 64      # 解密后跳过的字节数（padding）
_TR_RV = 32      # key material 长度
_TR_VP = 64      # XOR 数组长度
_TR_EM = 6       # key material 起始偏移
# XOR 混合的两个 64 字节常量数组
_TR_URE = bytes([82,9,106,213,48,54,165,56,191,64,163,158,129,243,215,251,
                 124,227,57,130,155,47,255,135,52,142,67,68,196,222,233,203,
                 84,123,148,50,166,194,35,61,238,76,149,11,66,250,195,78,
                 8,46,161,102,40,217,36,178,118,91,162,73,109,139,209,37])
_TR_DRE = bytes([31,221,168,51,136,7,199,49,177,18,16,89,39,128,236,95,
                 96,81,127,169,25,181,74,13,45,229,122,159,147,201,156,239,
                 160,224,59,77,174,42,245,176,200,235,187,60,131,83,153,97,
                 23,43,4,126,186,119,214,38,225,105,20,99,85,33,12,125])


# ============================================================
# Qoder 相关常量（桌面端无"每日签到"，等价动作是"活动积分领取"）
# ============================================================
# 数据目录候选：Qoder 是 Electron/Chromium 系，Windows 目录名是 bundle id 形态
# （实测 com.qoder.app.stable），macOS 在 ~/Library/Application Support 下同名。
QODER_DATA_DIRS = ("com.qoder.app.stable", "Qoder", "qoder")
QODER_AUTH_BASENAME = "auth.v1.dat"
QODER_LOCAL_STATE_BASENAME = "Local State"

# 凭据布局：auth.v1.dat = b"v10" + nonce(12) + 密文 + GCM tag(16)，AAD 为空；
# AES-256 密钥在 "Local State" 的 os_crypt.encrypted_key（base64 去 5 字节
# "DPAPI" 前缀后走当前用户作用域的 DPAPI 解封），解密复用文件里已有的
# 纯 Python _aes256_gcm_decrypt，零第三方依赖。

# 国际版与 CN 版是两套账号系统：同一 token 在 openapi.qoder.sh 返回 200、
# 在 openapi.qoder.com.cn 返回 401（本机实测）。按序探测、先通者定，
# QODER_API_BASE 可强制覆盖。
QODER_API_BASES = ("https://openapi.qoder.sh", "https://openapi.qoder.com.cn")
QODER_CAMPAIGNS_PATH = "/sash/api/v1/me/campaigns"
QODER_REFRESH_PATH = "/api/v1/deviceToken/refresh"

# access token 提前 5 分钟视为过期，给在途请求留余量
QODER_EXPIRY_MARGIN = 300.0

# claim 遇「根本没拿到响应」（连不上/超时）时的重试次数，与 Trae 侧同理：
# 业务错误码不重试，避免把账号打进风控。
QODER_CLAIM_RETRIES = 3


# ============================================================
# AES-128-CBC 解密（优先系统 openssl，缺失时回落到内嵌纯 Python 实现）
# ============================================================
# 为什么是两条路：
#   1. **优先**调用系统 openssl（`openssl enc -d -aes-128-cbc -nopad`）。macOS 自带
#      LibreSSL、主流 Linux 发行版、Git for Windows 都带它，且比纯 Python 快得多。
#   2. openssl 找不到、或调用失败时，自动回落到本文件内嵌的纯 Python AES-128
#      实现。这一步是为了让「零第三方依赖」名副其实：此前只有 openssl 一条路，
#      系统一旦没有 openssl（例如纯净的 Windows 环境），Trae 侧签到会直接断掉。
#   3. 两条路径契约完全一致：只解密，**保留 PKCS7 填充**，由调用方 _decrypt 剥离。
#
# openssl 可执行文件解析顺序：环境变量 OPENSSL_BIN（显式指定的完整路径）→ PATH。
# 若显式设置了 OPENSSL_BIN 却指向无效文件，会在 stderr 给出提示后继续回落，
# 既不静默忽略该环境变量，也不因此让签到失败。

_AES_TABLES = None   # (sbox, inv_sbox, mul) 懒加载缓存；只有走纯 Python 路径才需要


def _warn(msg):
    """把非致命提示写到 stderr；不写 stdout，避免污染 CLI 的 JSON 输出。"""
    try:
        sys.stderr.write("[signin] %s\n" % msg)
    except Exception:
        pass


def _find_openssl():
    """解析 openssl 可执行文件路径，找不到返回 None。

    顺序：环境变量 OPENSSL_BIN（完整路径）→ PATH 中的 openssl。
    """
    override = (os.environ.get("OPENSSL_BIN") or "").strip().strip('"').strip("'")
    if override:
        if os.path.isfile(override):
            return override
        _warn("OPENSSL_BIN 指向的不是有效文件：%s（已忽略，继续找 PATH 或走内嵌实现）"
              % override)
    return shutil.which("openssl")


def _gf_mul(a, b):
    """GF(2^8) 乘法，模 AES 的不可约多项式 x^8+x^4+x^3+x+1（即 0x11B）。"""
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p


def _aes_tables():
    """构建 AES 的 S-box / 逆 S-box / InvMixColumns 乘法表（只算一次）。

    S-box 不写死 256 个字节，而是按 FIPS-197 定义现算：
        S(x) = affine(x^-1)，x^-1 是 GF(2^8) 上的乘法逆（即 x 的 254 次幂）；
        逆 S-box 就是它的反函数。
    这样既免去几百字节常量抄错的风险，也能被标准测试向量逐字节验证。
    """
    global _AES_TABLES
    if _AES_TABLES is not None:
        return _AES_TABLES

    sbox = bytearray(256)
    for x in range(256):
        inv, base, e = 1, x, 254          # 平方-乘求 x^254 = x^-1（约定 0^-1 = 0）
        while e:
            if e & 1:
                inv = _gf_mul(inv, base)
            base = _gf_mul(base, base)
            e >>= 1
        s = inv                            # 仿射变换：s ^= rotl(inv, 1..4)，再异或 0x63
        for k in (1, 2, 3, 4):
            s ^= ((inv << k) | (inv >> (8 - k))) & 0xFF
        sbox[x] = s ^ 0x63

    inv_sbox = bytearray(256)
    for x in range(256):
        inv_sbox[sbox[x]] = x

    mul = {c: bytes(_gf_mul(c, v) for v in range(256))
           for c in (0x09, 0x0B, 0x0D, 0x0E)}

    _AES_TABLES = (bytes(sbox), bytes(inv_sbox), mul)
    return _AES_TABLES


def _aes128_expand_key(key, sbox):
    """AES-128 密钥扩展（FIPS-197 §5.2），返回 11 组轮密钥（各 16 字节）。"""
    w = [list(key[4 * i:4 * i + 4]) for i in range(4)]
    rcon = 0x01
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = [sbox[b] for b in (t[1:] + t[:1])]     # RotWord + SubWord
            t[0] ^= rcon
            rcon = _gf_mul(rcon, 0x02)
        w.append([w[i - 4][j] ^ t[j] for j in range(4)])
    return [bytes(sum(w[4 * r:4 * r + 4], [])) for r in range(11)]


def _aes128_inv_cipher(block, rk, inv_sbox, mul):
    """AES-128 逆密码（FIPS-197 §5.3），单分组 16 字节。

    状态按列优先展平存放：下标 = 行 + 4 * 列。
    """
    m9, mb, md, me = mul[0x09], mul[0x0B], mul[0x0D], mul[0x0E]

    def inv_shift_rows(st):
        for r in (1, 2, 3):                   # 后三行各自循环右移 r 位
            row = [st[r + 4 * c] for c in range(4)]
            row = row[-r:] + row[:-r]
            for c in range(4):
                st[r + 4 * c] = row[c]
        return st

    def inv_mix_columns(st):
        out = [0] * 16
        for c in range(4):
            a0, a1, a2, a3 = st[4 * c:4 * c + 4]
            out[4 * c + 0] = me[a0] ^ mb[a1] ^ md[a2] ^ m9[a3]
            out[4 * c + 1] = m9[a0] ^ me[a1] ^ mb[a2] ^ md[a3]
            out[4 * c + 2] = md[a0] ^ m9[a1] ^ me[a2] ^ mb[a3]
            out[4 * c + 3] = mb[a0] ^ md[a1] ^ m9[a2] ^ me[a3]
        return out

    st = [a ^ b for a, b in zip(block, rk[10])]
    for rnd in range(9, 0, -1):
        st = inv_shift_rows(st)
        st = [inv_sbox[b] for b in st]
        st = [a ^ b for a, b in zip(st, rk[rnd])]
        st = inv_mix_columns(st)
    st = inv_shift_rows(st)
    st = [inv_sbox[b] for b in st]
    return bytes(a ^ b for a, b in zip(st, rk[0]))


def _aes_cbc_decrypt_py(key, iv, ciphertext):
    """纯 Python 的 AES-128-CBC 解密（不剥 PKCS7 填充）。

    语义与 `openssl enc -d -aes-128-cbc -nopad` 一致：返回与密文等长的明文流，
    padding 原样保留。CBC 链接 = 解密后与本段之前的密文块（首段用 IV）异或。
    """
    sbox, inv_sbox, mul = _aes_tables()
    if len(key) != 16 or len(iv) != 16:
        raise RuntimeError("AES-128-CBC 需要 16 字节密钥与 16 字节 IV"
                           "（收到 key=%d, iv=%d）" % (len(key), len(iv)))
    if not ciphertext or len(ciphertext) % 16:
        raise RuntimeError("AES-128-CBC 密文长度必须是 16 的正整数倍（收到 %d 字节）"
                           % len(ciphertext))

    rk = _aes128_expand_key(key, sbox)
    out = bytearray()
    prev = iv
    for off in range(0, len(ciphertext), 16):
        blk = ciphertext[off:off + 16]
        plain = _aes128_inv_cipher(blk, rk, inv_sbox, mul)
        out += bytes(a ^ b for a, b in zip(plain, prev))
        prev = blk
    return bytes(out)


def _openssl_cbc_decrypt(exe, key, iv, ciphertext):
    """调用系统 openssl 做 AES-128-CBC 解密；失败时抛 RuntimeError。"""
    try:
        r = subprocess.run(
            [exe, "enc", "-d", "-aes-128-cbc",
             "-K", key.hex(), "-iv", iv.hex(), "-nopad"],
            input=ciphertext, capture_output=True, timeout=10
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("openssl 解密超时")
    except OSError as e:
        raise RuntimeError("openssl 调用失败: %s" % e)
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace") if r.stderr else "(无 stderr)"
        raise RuntimeError("openssl 解密失败 (exit=%d): %s" % (r.returncode, err[:300]))
    if len(r.stdout) < 16:
        raise RuntimeError("openssl 解密返回数据太短（%d 字节）" % len(r.stdout))
    return r.stdout


def _aes_cbc_decrypt(key, iv, ciphertext):
    """AES-128-CBC 解密（不剥 PKCS7 填充）。

    返回原文（含填充）。调用方自行剥离 padding。

    优先走系统 openssl；openssl 不可用或调用出错时，回落到内嵌纯 Python 实现
    （回落原因会写到 stderr，便于排查）。
    """
    exe = _find_openssl()
    if exe:
        try:
            return _openssl_cbc_decrypt(exe, key, iv, ciphertext)
        except RuntimeError as e:
            _warn("%s（已回落到内嵌纯 Python AES 实现）" % e)
    return _aes_cbc_decrypt_py(key, iv, ciphertext)


# ============================================================
# WorkBuddy 凭据解封（桌面端 at-rest 加密格式）
# ============================================================
# 背景：WorkBuddy 桌面端更新后，凭据文件 workbuddy-desktop.info 里的敏感字段不再是
# 明文，而是加密信封：
#     {"$wbEncrypted": 1, "envelope": "<base64>"}
# envelope 解 base64 后是：
#     {"suite":1,"keyId":"...","nonce":"<b64 12B>","authTag":"<b64 16B>","ciphertext":"<b64>"}
# 也就是一组 AES-256-GCM 密文。旧代码直接把这个 dict 拼进 "Bearer %s"，实际发出去的
# 是 Python 字典字面量，服务端当然回 401 —— 于是被误报成"登录态已失效"，而用户其实
# 登录得好好的。
#
# 密钥：桌面端把 32 字节 at-rest 密钥放在自己的原生绑定里，唯一入口是以
# ELECTRON_RUN_AS_NODE 模式运行 WorkBuddy.exe，调
#     process._linkedBinding('electron_browser_workbuddy_storage').loggerGet()
# 拿到 atRestSecretKey（base64），sha256 之后才是实际对称密钥；该密钥的 sha256 前
# 16 字节十六进制即信封里的 keyId，用于校验取到的密钥是否配对。
#
# 解封顺序：先纯 Python AES-256-GCM（保住"零第三方依赖"），失败再回落让
# WorkBuddy.exe 用它自带的 Node crypto 解一遍（两条路的 AAD 逐字节相同，区别只在
# 密码学实现由谁执行，用于排除自实现有 bug 的可能）。两条都失败才报
# ENCRYPTED_AUTH，并把真正的原因写出来。

WB_ENVELOPE_MARKER = "$wbEncrypted"
WB_EXE_ENV = "WORKBUDDY_EXE"

_WB_SUITE = 1
_WB_FORMAT_ID = "WBEV1"     # AAD 里的标准格式标识
_WB_SCHEME_SYM = "sym-v1"   # AAD 里的对称方案名
_WB_FRAMING_FIELD = 2       # AAD 里的 framing：file=1 / field=2 / record=3 / stream=4

_WB_KEY_JS = ("const b=process._linkedBinding('electron_browser_workbuddy_storage');"
              "process.stdout.write('@@'+b.loggerGet())")

# 回落用：同一套 AAD，交给 WorkBuddy.exe 自带的 Node crypto 做 GCM
_WB_UNSEAL_JS = (
    "const c=require('crypto');"
    "const b=process._linkedBinding('electron_browser_workbuddy_storage');"
    "const key=c.createHash('sha256').update(JSON.parse(b.loggerGet()).atRestSecretKey).digest();"
    "const d=JSON.parse(process.argv[1]);"
    "const dp=c.createDecipheriv('aes-256-gcm',key,Buffer.from(d.nonce,'base64'));"
    "dp.setAAD(Buffer.from(process.argv[2],'hex'));"
    "dp.setAuthTag(Buffer.from(d.tag,'base64'));"
    "process.stdout.write('@@'+Buffer.concat(["
    "dp.update(Buffer.from(d.ct,'base64')),dp.final()]).toString('utf8'));")

_AES_FWD_MUL = None    # 加密侧 MixColumns 的 ×2 / ×3 表（懒加载）
_WB_KEY_CACHE = None   # 已取到的 at-rest 密钥，避免 webapp 长驻时反复拉起 exe


class EncryptedAuthError(Exception):
    """WorkBuddy 凭据已加密，且本次未能解封（原因见 message）。"""


def _aes_fwd_mul():
    """加密侧 MixColumns 需要的 xtime 表（×2 / ×3）。"""
    global _AES_FWD_MUL
    if _AES_FWD_MUL is None:
        _AES_FWD_MUL = (bytes(_gf_mul(2, v) for v in range(256)),
                        bytes(_gf_mul(3, v) for v in range(256)))
    return _AES_FWD_MUL


def _aes256_expand_key(key, sbox):
    """AES-256 密钥扩展（FIPS-197 §5.2），返回 15 组轮密钥（各 16 字节）。

    与 AES-128 的差别只有两处：Nk=8（每 8 个字一轮）与 Nr=14；且 i % 8 == 4 时要
    额外做一次 SubWord（AES-128 没有这一步）。
    """
    nk, rounds = 8, 14
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    rcon = 0x01
    for i in range(nk, 4 * (rounds + 1)):
        t = list(w[i - 1])
        if i % nk == 0:
            t = [sbox[b] for b in (t[1:] + t[:1])]    # RotWord + SubWord
            t[0] ^= rcon
            rcon = _gf_mul(rcon, 0x02)
        elif i % nk == 4:
            t = [sbox[b] for b in t]                  # AES-256 独有
        w.append([w[i - nk][j] ^ t[j] for j in range(4)])
    return [bytes(sum(w[4 * r:4 * r + 4], [])) for r in range(rounds + 1)]


def _aes256_encrypt_block(block, rk, sbox, m2, m3):
    """AES-256 正向密码（FIPS-197 §5.1），单分组 16 字节。GCM 的 CTR 需要它。

    状态按列优先展平：下标 = 行 + 4 * 列。
    """
    def shift_rows(st):
        out = list(st)
        for r in (1, 2, 3):                      # 后三行各自循环左移 r 位
            row = [st[r + 4 * c] for c in range(4)]
            row = row[r:] + row[:r]
            for c in range(4):
                out[r + 4 * c] = row[c]
        return out

    def mix_columns(st):
        out = [0] * 16
        for c in range(4):
            a0, a1, a2, a3 = st[4 * c:4 * c + 4]
            out[4 * c + 0] = m2[a0] ^ m3[a1] ^ a2 ^ a3
            out[4 * c + 1] = a0 ^ m2[a1] ^ m3[a2] ^ a3
            out[4 * c + 2] = a0 ^ a1 ^ m2[a2] ^ m3[a3]
            out[4 * c + 3] = m3[a0] ^ a1 ^ a2 ^ m2[a3]
        return out

    st = [a ^ b for a, b in zip(block, rk[0])]
    for rnd in range(1, 14):
        st = mix_columns(shift_rows([sbox[b] for b in st]))
        st = [a ^ b for a, b in zip(st, rk[rnd])]
    st = shift_rows([sbox[b] for b in st])
    return bytes(a ^ b for a, b in zip(st, rk[14]))


# GCM 的多项式约简常量：11100001 || 0^120
_GHASH_R = 0xE1 << 120


def _gf_mul128(x, y):
    """GF(2^128) 乘法（SP 800-38D §6.3）。x、y 都是 128 位整数。"""
    z, v = 0, y
    for i in range(128):
        if (x >> (127 - i)) & 1:
            z ^= v
        if v & 1:
            v = (v >> 1) ^ _GHASH_R
        else:
            v >>= 1
    return z


def _gcm_pad(data):
    """按 GCM 要求补 0 到 16 字节整数倍（空数据保持为空）。"""
    if not data:
        return b""
    rem = len(data) % 16
    return data if not rem else data + bytes(16 - rem)


def _ghash(h_int, data):
    """GHASH：逐块与 H 做 GF(2^128) 乘法后累加（SP 800-38D §6.4）。"""
    y = 0
    for off in range(0, len(data), 16):
        blk = data[off:off + 16]
        if len(blk) < 16:
            blk = blk + bytes(16 - len(blk))
        y = _gf_mul128(y ^ int.from_bytes(blk, "big"), h_int)
    return y


def _aes256_gcm_decrypt(key, nonce, aad, ciphertext, tag):
    """纯 Python 的 AES-256-GCM 解密（SP 800-38D）。认证失败抛 RuntimeError。"""
    if len(key) != 32:
        raise RuntimeError("AES-256-GCM 需要 32 字节密钥（收到 %d）" % len(key))
    if len(nonce) != 12:
        raise RuntimeError("只支持 12 字节 IV 的 GCM（收到 %d）" % len(nonce))
    if len(tag) != 16:
        raise RuntimeError("GCM 认证标签必须是 16 字节（收到 %d）" % len(tag))

    sbox = _aes_tables()[0]
    m2, m3 = _aes_fwd_mul()
    rk = _aes256_expand_key(key, sbox)
    # H = E_K(0^128)，GHASH 的哈希子密钥
    h_int = int.from_bytes(_aes256_encrypt_block(bytes(16), rk, sbox, m2, m3), "big")
    # S = E_K(J0)，J0 = IV || 0x00000001（12 字节 IV 的标准情形），认证标签要异或它
    s_int = int.from_bytes(
        _aes256_encrypt_block(nonce + (1).to_bytes(4, "big"), rk, sbox, m2, m3), "big")

    # CTR：数据块从 inc32(J0) 开始，即计数器 2、3、…
    out = bytearray()
    for idx, off in enumerate(range(0, len(ciphertext), 16), start=2):
        ks = _aes256_encrypt_block(nonce + idx.to_bytes(4, "big"), rk, sbox, m2, m3)
        out += bytes(a ^ b for a, b in zip(ciphertext[off:off + 16], ks))

    # 认证：GHASH(A || pad || C || pad || len(A) || len(C))，长度以比特计
    blob = (_gcm_pad(aad) + _gcm_pad(ciphertext)
            + (len(aad) * 8).to_bytes(8, "big") + (len(ciphertext) * 8).to_bytes(8, "big"))
    if _ghash(h_int, blob) ^ s_int != int.from_bytes(tag, "big"):
        raise RuntimeError("GCM 认证标签校验失败（密钥不匹配，或信封格式已变）")
    return bytes(out)


def _wb_is_envelope(v):
    """判断某个字段值是不是加密信封。"""
    return (isinstance(v, dict) and v.get(WB_ENVELOPE_MARKER)
            and isinstance(v.get("envelope"), str))


def _wb_aad(key_id):
    """构造成 AAD（附加认证数据）。

    布局：域分隔 "WB-AAD\\0" + 版本(1) + lenprefix("WBEV1") + lenprefix(scheme)
          + uint32be(suite) + lenprefix(keyId) + framing(1B) + 两个"可选字段不存在"(各 1B)
    lenprefix(x) = uint32be(len) + 原始字节；尾部两个 0x00 分别表示"可选 uint64 序号
    不存在"与"最终可选字段不存在"。
    """
    def lp(s):
        b = s.encode("utf-8")
        return len(b).to_bytes(4, "big") + b

    return (b"WB-AAD\x00" + bytes([0x01]) + lp(_WB_FORMAT_ID) + lp(_WB_SCHEME_SYM)
            + _WB_SUITE.to_bytes(4, "big") + lp(key_id)
            + bytes([_WB_FRAMING_FIELD]) + bytes([0x00]) + bytes([0x00]))


def _wb_parse_exe_path(raw):
    """从注册表的 DisplayIcon / UninstallString 里取出主程序路径。

    形如 `D:\\workbuddy\\WorkBuddy.exe,0` 或 `"D:\\workbuddy\\Uninstall WorkBuddy.exe" /allusers`。
    """
    if not raw:
        return None
    s = raw.strip()
    if s.startswith('"'):
        end = s.find('"', 1)
        s = s[1:end] if end > 0 else s.strip('"')
    else:
        for suffix in (",0", " /allusers", " /s"):
            if s.lower().endswith(suffix):
                s = s[: -len(suffix)]
                break
    if os.path.isfile(s) and os.path.basename(s).lower() == "workbuddy.exe":
        return s
    # 卸载器与主程序同目录
    guess = os.path.join(os.path.dirname(s), "WorkBuddy.exe")
    return guess if os.path.isfile(guess) else None


def _wb_exe_from_registry():
    """Windows：从卸载项里找 WorkBuddy 主程序（安装位置由用户自选，猜不出来）。"""
    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:
        return None
    roots = (
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    for hive, path in roots:
        try:
            with winreg.OpenKey(hive, path) as k:
                for i in range(winreg.QueryInfoKey(k)[0]):
                    try:
                        with winreg.OpenKey(k, winreg.EnumKey(k, i)) as sub:
                            name = str(winreg.QueryValueEx(sub, "DisplayName")[0] or "")
                            # "WorkBuddy 5.6.2" 命中；"WorkBuddy AI ..." 是另一个产品，排除
                            if not name.startswith("WorkBuddy ") or name.startswith("WorkBuddy AI"):
                                continue
                            for field in ("DisplayIcon", "UninstallString"):
                                try:
                                    raw = str(winreg.QueryValueEx(sub, field)[0] or "")
                                except OSError:
                                    continue
                                hit = _wb_parse_exe_path(raw)
                                if hit:
                                    return hit
                    except OSError:
                        continue
        except OSError:
            continue
    return None


def _find_workbuddy_exe():
    """定位 WorkBuddy 桌面端主程序，找不到返回 None（可用 WORKBUDDY_EXE 显式指定）。"""
    override = (os.environ.get(WB_EXE_ENV) or "").strip().strip('"').strip("'")
    if override:
        return override if os.path.isfile(override) else None
    hit = _wb_exe_from_registry()
    if hit:
        return hit
    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    pf = os.environ.get("ProgramFiles") or r"C:\Program Files"
    pf86 = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
    for c in (os.path.join(local, "Programs", "WorkBuddy", "WorkBuddy.exe"),
              os.path.join(pf, "WorkBuddy", "WorkBuddy.exe"),
              os.path.join(pf86, "WorkBuddy", "WorkBuddy.exe")):
        if os.path.isfile(c):
            return c
    for pattern in (os.path.join(local, "Programs", "*", "WorkBuddy.exe"),
                    os.path.join(home, "WorkBuddy", "WorkBuddy.exe")):
        for hit in sorted(glob.glob(pattern)):
            if os.path.isfile(hit):
                return hit
    return None


def _wb_run_js(exe, js, args=(), timeout=30):
    """以 ELECTRON_RUN_AS_NODE 模式跑 WorkBuddy.exe 执行一段 JS，返回 stdout。

    这样能用上桌面端自己的原生绑定（密钥库）与 Node crypto，无需任何第三方依赖。
    """
    env = dict(os.environ, ELECTRON_RUN_AS_NODE="1")
    cmd = [exe, "-e", js]
    if args:
        cmd += ["--"] + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise RuntimeError("WorkBuddy.exe 执行超时（%ds）" % timeout)
    except OSError as e:
        raise RuntimeError("WorkBuddy.exe 无法执行：%s" % e)
    if r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", "replace").strip()[-300:]
        raise RuntimeError("WorkBuddy.exe 执行失败（exit=%d）：%s" % (r.returncode, err or "无 stderr"))
    return r.stdout.decode("utf-8", "replace")


def _wb_at_rest_key(exe, refresh=False):
    """取 at-rest 对称密钥（sha256(atRestSecretKey)），结果缓存供长驻进程复用。"""
    global _WB_KEY_CACHE
    if _WB_KEY_CACHE is not None and not refresh:
        return _WB_KEY_CACHE
    out = _wb_run_js(exe, _WB_KEY_JS)
    i = out.find("@@")
    if i < 0:
        raise RuntimeError("未从 WorkBuddy.exe 取到密钥库内容")
    try:
        sk = json.loads(out[i + 2:])["atRestSecretKey"]
    except Exception as e:
        raise RuntimeError("WorkBuddy 密钥库格式无法识别（%s: %s）" % (type(e).__name__, e))
    if not isinstance(sk, str) or not sk:
        raise RuntimeError("WorkBuddy 密钥库里没有 atRestSecretKey")
    _WB_KEY_CACHE = hashlib.sha256(sk.encode("utf-8")).digest()
    return _WB_KEY_CACHE


def _wb_unseal_via_exe(exe, hdr, aad):
    """回落路径：把同一份 AAD/密文交给 WorkBuddy.exe 自带的 Node crypto 解。"""
    payload = json.dumps({"nonce": hdr["nonce"], "tag": hdr["authTag"],
                          "ct": hdr["ciphertext"]})
    out = _wb_run_js(exe, _WB_UNSEAL_JS, args=(payload, aad.hex()))
    i = out.find("@@")
    if i < 0:
        raise RuntimeError("回落解密没有返回结果")
    return out[i + 2:]


def _wb_unseal(env):
    """解封一个 $wbEncrypted 信封，返回明文字符串；失败抛 RuntimeError。"""
    try:
        hdr = json.loads(base64.b64decode(env.get("envelope") or ""))
        key_id = hdr["keyId"]
        suite = hdr.get("suite")
        nonce = base64.b64decode(hdr["nonce"])
        tag = base64.b64decode(hdr["authTag"])
        ct = base64.b64decode(hdr["ciphertext"])
    except Exception as e:
        raise RuntimeError("信封结构无法解析（%s: %s）" % (type(e).__name__, e))
    if suite != _WB_SUITE:
        raise RuntimeError("未知的信封 suite=%r" % (suite,))
    if len(nonce) != 12 or len(tag) != 16:
        raise RuntimeError("信封 nonce/authTag 长度异常（%d/%d 字节）" % (len(nonce), len(tag)))

    exe = _find_workbuddy_exe()
    if not exe:
        raise RuntimeError("未找到 WorkBuddy.exe，取不到解密密钥"
                           "（可用环境变量 %s 指定完整路径）" % WB_EXE_ENV)

    reasons = []
    try:
        aad = _wb_aad(key_id)
        key = _wb_at_rest_key(exe)
        if hashlib.sha256(key).hexdigest()[:16] != key_id:
            # 缓存里的密钥可能已被桌面端轮换，重取一次再判
            key = _wb_at_rest_key(exe, refresh=True)
            if hashlib.sha256(key).hexdigest()[:16] != key_id:
                raise RuntimeError("密钥指纹与信封 keyId 不一致")
        return _aes256_gcm_decrypt(key, nonce, aad, ct, tag).decode("utf-8")
    except Exception as e:
        reasons.append("纯 Python 解密失败：%s" % e)
    try:
        return _wb_unseal_via_exe(exe, hdr, _wb_aad(key_id))
    except Exception as e:
        reasons.append("回落 WorkBuddy.exe 解密失败：%s" % e)
    raise RuntimeError("；".join(reasons))


def _wb_unseal_session(session):
    """把凭据里的加密信封字段解封成明文；旧格式（本就是明文）原样返回。

    只解脚本真正用到的 accessToken：nickname / phoneNumber 等不参与鉴权，不碰它们
    （少一次解密，少一份明文暴露面）。
    """
    auth = session.get("auth")
    if not isinstance(auth, dict):
        return session
    token = auth.get("accessToken")
    if _wb_is_envelope(token):
        try:
            auth["accessToken"] = _wb_unseal(token)
        except Exception as e:
            raise EncryptedAuthError(str(e))
    return session


# ============================================================
# Trae CN 凭据解密
# ============================================================
def _sha512(data):
    return hashlib.sha512(data).digest()


def _xor_ure_dre():
    """XOR 混合两个常量数组。"""
    return bytes(a ^ b for a, b in zip(_TR_URE, _TR_DRE))


def decrypt_trae_auth(b64_str):
    """解密 storage.json 中的 iCubeAuthInfo://icube.cloudide 值。

    算法（对齐 checkin.js）：
      1. base64 解码得到字节串 t
      2. 取 t[Em:Em+Rv]（32 字节）作为 key_material，SHA-512 得到 sha
      3. 构造 comb = sha || xor(ure, dre)（长度 64+64=128）
      4. hash = SHA-512(comb)
      5. aes_key = hash[0:16], iv = hash[16:32]
      6. 密文 ct = t[Em+Rv:]
      7. AES-128-CBC 解密 ct（优先系统 openssl，缺失时用内嵌纯 Python 实现）
      8. 跳过前 rh=64 字节，剩下的是 JSON 明文

    返回 JSON 对象（dict）。
    """
    if not isinstance(b64_str, str):
        raise ValueError("TRAE_AUTH_INFO 不是字符串")
    try:
        t = base64.b64decode(b64_str)
    except Exception as e:
        raise ValueError("base64 解码失败: %s" % e)

    if len(t) < _TR_EM + _TR_RV + 16:
        raise ValueError("TRAE 密文太短（%d 字节），不是有效格式" % len(t))

    key_material = t[_TR_EM:_TR_EM + _TR_RV]
    sha = _sha512(key_material)
    xor = _xor_ure_dre()
    comb = sha + xor
    hash_ = _sha512(comb)
    aes_key = hash_[:_TR_Q8]
    iv = hash_[_TR_Q8:_TR_Q8 + _TR_WP]

    ct = t[_TR_EM + _TR_RV:]
    # 密文必须是 16 的倍数（CBC 要求）
    ct = ct[: (len(ct) // 16) * 16]
    if not ct:
        raise ValueError("密文为空")

    dec = _aes_cbc_decrypt(aes_key, iv, ct)
    # 两条解密路径都保留 PKCS7 填充（对齐 openssl 的 -nopad），先剥填充再取有效负载
    if len(dec) >= 16:
        pad = dec[-1]
        if 1 <= pad <= 16:
            dec = dec[:-pad]
    if len(dec) < _TR_RH:
        raise ValueError("解密后长度不足（%d < %d）" % (len(dec), _TR_RH))
    plain = dec[_TR_RH:]
    try:
        return json.loads(plain.decode("utf-8"))
    except Exception as e:
        raise ValueError("解密后不是合法 JSON: %s" % e)


# ============================================================
# Trae CN 凭据文件探测
# ============================================================
def find_trae_storage_file():
    """探测本机 Trae 桌面端的 storage.json，返回 (path_or_None, looked_in)。"""
    override = os.environ.get("TRAE_AUTH_FILE")
    if override:
        paths = [p.strip() for p in override.split(";") if p.strip()]
        for p in paths:
            if os.path.exists(p):
                return p, paths
        return None, paths

    override_dir = os.environ.get("TRAE_STORAGE_DIR")
    if override_dir:
        p = os.path.join(override_dir, "User", "globalStorage", "storage.json")
        return (p if os.path.exists(p) else None), [p]

    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    appdata = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")

    candidates = []
    # Windows：APPDATA\{product}\User\globalStorage\storage.json
    for d in TRAE_WIN_APPDATA_DIRS:
        candidates.append(os.path.join(appdata, d, TRAE_STORAGE_SUBDIR))
        candidates.append(os.path.join(local, d, TRAE_STORAGE_SUBDIR))
    # macOS: ~/Library/Application Support/{product}/User/globalStorage/storage.json
    for d in TRAE_MACOS_DIRS:
        candidates.append(os.path.join(home, "Library", "Application Support", d, TRAE_STORAGE_SUBDIR))
    # Linux XDG
    for d in ("Trae", "trae", "trab"):
        candidates.append(os.path.join(home, ".config", d, TRAE_STORAGE_SUBDIR))

    for c in candidates:
        if os.path.exists(c):
            return c, candidates
    return None, candidates


def _trae_aha_device_id(storage):
    """从 storage.json 的 ``iCubeAuthInfo://icube-dc:<did>`` 键后缀取 AHA deviceId。

    这是官方客户端签到时真正上报的 x-device-id，来源是 native ahaDeviceService，
    与 telemetry.devDeviceId（VS Code 机器码）完全不同。
    """
    for k in storage:
        if isinstance(k, str) and k.startswith(TRAE_DC_KEY_PREFIX):
            did = k[len(TRAE_DC_KEY_PREFIX):].strip()
            if did:
                return did
    return None


_COMMON_PARAMS_RE = re.compile(r'"common_params":\s*(\{.*?\})\}')
_common_params_cache = None


def _trae_common_params(storage_path=None):
    """读官方客户端上报的 common_params 原值，拿 device_model / os_version 等。

    renderer.log 里有一行形如
      "common_params": {"cpu":"AMD","device_id":"416...","machine_id":"9f1...",
                        "device_model":"System Product Name","os_name":"windows",
                        "os_version":"Windows 11 Pro for Workstations"}
    这是唯一能拿到「与官方字节级一致」的设备型号/系统版本串的途径。日志被清理
    时返回 {}，调用方按「取不到就不带该头」降级——官方 fb() 也是这个行为。

    结果按 storage_path 记忆化，避免同一次运行里重复扫盘。
    """
    global _common_params_cache
    if _common_params_cache is not None:
        return _common_params_cache

    cands = []
    if storage_path:
        # <product>\User\globalStorage\storage.json → 上溯 3 层到 <product>
        pd = os.path.abspath(storage_path)
        for _ in range(3):
            pd = os.path.dirname(pd)
        lg = os.path.join(pd, "logs")
        if os.path.isdir(lg):
            cands += glob.glob(os.path.join(lg, "*", "window*", "renderer.log"))
            cands += glob.glob(os.path.join(lg, "*", "renderer.log"))
    appdata = os.environ.get("APPDATA")
    if appdata and os.path.isdir(appdata):
        cands += glob.glob(os.path.join(appdata, "*", "logs", "*", "window*", "renderer.log"))

    _common_params_cache = {}
    if not cands:
        return _common_params_cache
    # 只看最新的若干个，别把签到跑成全盘扫描
    try:
        cands = sorted(set(cands), key=os.path.getmtime, reverse=True)[:12]
    except Exception:
        cands = list(set(cands))[:12]

    for p in cands:
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                s = f.read()
        except Exception:
            continue
        for m in _COMMON_PARAMS_RE.finditer(s):
            try:
                cp = json.loads(m.group(1))
            except Exception:
                continue
            if isinstance(cp, dict) and cp.get("device_id"):
                _common_params_cache = cp
                return cp
    return _common_params_cache


_app_version_cache = None


def _trae_app_version():
    """从已安装的 Trae 客户端 resources/app/product.json 读 appVersion。"""
    global _app_version_cache
    if _app_version_cache is not None:
        return _app_version_cache or None

    roots = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(os.path.join(local, "Programs"))
    if sys.platform == "darwin":
        roots.append("/Applications")
    for root in roots:
        if not os.path.isdir(root):
            continue
        try:
            names = os.listdir(root)
        except Exception:
            continue
        for name in names:
            if "trae" not in name.lower():
                continue
            pj = os.path.join(root, name, "resources", "app", "product.json")
            if not os.path.exists(pj):
                continue
            try:
                with open(pj, "r", encoding="utf-8") as f:
                    v = json.load(f).get("appVersion")
            except Exception:
                continue
            if v:
                _app_version_cache = str(v)
                return _app_version_cache
    _app_version_cache = ""
    return None


def _trae_os_name():
    """官方 os_name 取值：小写的 windows / darwin(实为 macos) / linux。"""
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return "windows"


def load_trae_session(storage_path):
    """读 storage.json，抽取 token + 官方设备档案，返回 dict 或抛异常。"""
    try:
        with open(storage_path, "r", encoding="utf-8") as f:
            storage = json.load(f)
    except Exception as e:
        raise ValueError("读取 storage.json 失败: %s" % e)

    b64 = storage.get(TRAE_AUTH_KEY)
    if not b64:
        raise ValueError("storage.json 缺少 %s 键（可能未登录）" % TRAE_AUTH_KEY)

    auth_info = decrypt_trae_auth(b64)
    token = auth_info.get("token")
    if not token:
        raise ValueError("解密后无 token 字段")
    region = None
    if isinstance(auth_info.get("userRegion"), dict):
        region = auth_info["userRegion"].get("region")

    # 设备档案：优先用官方日志里的原值，取不到就按平台计算，再取不到就留空
    # （留空的头在 build_trae_headers 里直接不发，与官方 fb() 的"有才带"一致）。
    cp = _trae_common_params(storage_path)
    device_id = _trae_aha_device_id(storage) or cp.get("device_id")
    return {
        "token": token,
        "region": region,
        "userId": auth_info.get("userId") or auth_info.get("account"),
        "machineId": cp.get("machine_id") or storage.get("telemetry.machineId"),
        "deviceId": device_id,
        "deviceModel": cp.get("device_model"),
        "osName": cp.get("os_name") or _trae_os_name(),
        "osVersion": cp.get("os_version"),
        "appVersion": _trae_app_version(),
    }


# 自动签到出站前的随机延迟（秒），破除"固定秒点跑"的机器节奏。上限取 30s：
# 远小于 420s 的运行时预算，也避免手动"立即签到"时让人等太久。
JITTER_LO, JITTER_HI = 3.0, 30.0


def _ua_arch():
    """根据本机平台给出 UA 里的 OS 段；未知平台回落 Windows。"""
    if sys.platform == "darwin":
        return "Macintosh; Intel Mac OS X 10_15_7"
    if sys.platform.startswith("linux"):
        return "X11; Linux x86_64"
    return "Windows NT 10.0; Win64; x64"


def _electron_ua(product):
    """伪装成与官方桌面端同构的 Electron UA，避免被风控一眼判为脚本。

    真实客户端是 Electron/Chromium，UA 形如 "Mozilla/5.0 (...) AppleWebKit/537.36
    (KHTML, like Gecko) {Product}/x.y.z Chrome/xxx Electron/xx.x.x Safari/537.36"。
    原先的 "WorkBuddy" / "Trae CN Auto-Signin" 这类精简串是明显的机器指纹；
    这里生成结构完整、可读自然的 Electron UA。版本取一个稳定的 Chrome/Electron
    组合——风控粒度通常只到"是否 Electron 结构"这一层，不必长周期跟踪官方版本。
    """
    return ("Mozilla/5.0 (%s) AppleWebKit/537.36 (KHTML, like Gecko) "
            "%s/1.0.0 Chrome/131.0.6778.204 Electron/32.2.0 Safari/537.36"
            % (_ua_arch(), product))


def _sleep_jitter():
    """自动签到出站前加入小幅随机延迟，弱化固定时序特征。"""
    time.sleep(JITTER_LO + random.random() * (JITTER_HI - JITTER_LO))


def _quote_header(value):
    """官方客户端经 TTNet 发请求时，含空格的头值会被 URL 编码
    （如 "System Product Name" → "System%20Product%20Name"）。这里保持一致。
    """
    return urllib.parse.quote(str(value), safe="")


def build_trae_headers(session):
    """按官方桌面端 bb()+cb()+fb() 的形态组装 checkin 请求头。

    fb() 的规则是「取到才带」，所以缺项直接省略而不是塞占位值——占位值反而
    是明显的机器指纹。此前多带的 userId / userRegion / X-User-Region 三个头
    在官方请求里并不存在，一并去掉，做到与真实客户端字节级对齐。
    """
    h = {
        "Accept": "application/json",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Content-Type": "application/json",
        "User-Agent": _electron_ua("Trae"),
        "Authorization": "Cloud-IDE-JWT %s" % session["token"],
        # TTNet 层固定附加的超时声明（毫秒），一并复刻
        "x-rust-request-timeout": "30000",
    }
    if session.get("deviceId"):
        h["x-device-id"] = str(session["deviceId"])
    if session.get("deviceModel"):
        h["x-device-brand"] = _quote_header(session["deviceModel"])
    if session.get("osName"):
        h["x-device-type"] = _quote_header(session["osName"])
    if session.get("osVersion"):
        h["x-os-version"] = _quote_header(session["osVersion"])
    if session.get("appVersion"):
        h["x-app-version"] = str(session["appVersion"])
    if session.get("machineId"):
        h["x-machine-id"] = str(session["machineId"])
    return h


def _trae_request(url, headers, method="POST", payload=None, timeout=30):
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, {"raw": raw[:500]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:500]}
    except urllib.error.URLError as e:
        return CODE_NO_NETWORK, {"error": str(e.reason)}
    except Exception as e:
        return CODE_NO_NETWORK, {"error": str(e)}


def _trae_payload():
    """官方 checkin 接口的固定请求体。"""
    return {"req_source": TRAE_REQ_SOURCE}


def trae_status(headers):
    """查今日签到状态。"""
    code, body = _trae_request(TRAE_STATUS_URL, headers, "POST", _trae_payload())
    if code == 200 and isinstance(body, dict):
        return {
            "checked_in": bool(body.get("checked_in")),
            "enable": bool(body.get("enable", True)),
            "credits": int(body.get("credits") or 0),
        }
    return {"error": code, "body": body}


def trae_claim(headers):
    """执行签到（幂等：已签时返回 code!=0）。

    曾经这里对 9074「当前参与用户太多」做重试，但 9074 的真实成因不是限流，
    而是请求形态与官方客户端不一致：deviceId 用了 telemetry.devDeviceId、
    且没有带 {"req_source":1} 请求体。修正后 claim 直接返回 code 0，重试
    只会白打接口、还可能真的把账号打进风控。因此现在只对「根本没拿到响应」
    的网络错误重试，业务错误码一律当场定论并如实上报。
    """
    last = None
    for attempt in range(TRAE_CLAIM_RETRIES):
        code, body = _trae_request(TRAE_CLAIM_URL, headers, "POST", _trae_payload())
        if code == CODE_NO_NETWORK and attempt + 1 < TRAE_CLAIM_RETRIES:
            last = {"ok": False, "http": code, "body": body}
            time.sleep(2.0 + attempt * 2.0)
            continue
        if code != 200 or not isinstance(body, dict):
            return {"ok": False, "http": code, "body": body}
        api_code = body.get("code")
        msg = body.get("message", "")
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        # 成功响应可能是 {"code":0,"message":"success"}（无 data），
        # 也可能带 data.credits，两种都兜住。
        credits = int((data or {}).get("credits") or body.get("credits") or 0)
        if api_code == 0:
            return {"ok": True, "http": code, "code": 0,
                    "message": msg, "credits": credits}
        return {"ok": False, "http": code, "code": api_code,
                "message": msg, "credits": credits}
    return last or {"ok": False, "http": CODE_NO_NETWORK,
                    "message": "network unreachable"}


# ============================================================
# Qoder 活动积分领取：凭据解密 + campaigns/claim 接口
# ============================================================
def _q_dpapi_unprotect(data):
    """Windows DPAPI（CryptUnprotectData）解封 os_crypt 密钥，返回 32 字节 AES key。

    Qoder 与所有 Chromium 系桌面端一样，把 Local State 里的密钥又包了一层
    "当前用户"作用域的 DPAPI——密文只在本机本用户可解，把 auth.v1.dat 拷到
    别的机器上没有意义，所以这条路径是 Windows-only；其它平台如实抛错。
    """
    if sys.platform != "win32":
        raise RuntimeError("解密 Qoder 凭据依赖 Windows DPAPI，当前平台不支持，"
                           "请在 Windows 上运行 qoder 命令")
    import ctypes
    import ctypes.wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    # buf 显式持有缓冲区，由 blob_in 结构体间接保活（实机验证过的写法）
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), buf)
    blob_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0,
            ctypes.byref(blob_out)):
        raise RuntimeError("CryptUnprotectData 失败（WinError %d），"
                           "请在加密时所用的 Windows 账户下运行"
                           % ctypes.windll.kernel32.GetLastError())
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def find_qoder_dir():
    """探测同时含 auth.v1.dat 与 Local State 的 Qoder 数据目录。

    返回 (dir_or_None, looked_in)，形态与 find_trae_storage_file 对齐。
    """
    override = os.environ.get("QODER_AUTH_FILE")
    if override:
        ok = os.path.exists(override)
        return (os.path.dirname(override) if ok else None), [override]

    override_dir = os.environ.get("QODER_STORAGE_DIR")
    if override_dir:
        candidates = [override_dir]
    else:
        home = os.path.expanduser("~")
        local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        appdata = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
        candidates = []
        for d in QODER_DATA_DIRS:
            candidates.append(os.path.join(appdata, d))
            candidates.append(os.path.join(local, d))
            candidates.append(os.path.join(home, "Library", "Application Support", d))
            candidates.append(os.path.join(home, ".config", d))

    for c in candidates:
        if (os.path.exists(os.path.join(c, QODER_AUTH_BASENAME))
                and os.path.exists(os.path.join(c, QODER_LOCAL_STATE_BASENAME))):
            return c, candidates
    return None, candidates


def _q_parse_time(value):
    """把 expiresAt / expires_at 统一解析成 epoch 秒。

    auth.v1.dat 里是 ISO 字符串（如 "2026-10-19T09:18:32Z"，曾被当作 epoch 秒
    比较踩过类型坑）；refresh 接口的 expires_at 则可能是秒/毫秒时间戳。三种
    形态在这里一次收敛，认不出就返回 None（按"不过期"降级，不误杀会话）。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000.0 if v > 1e11 else v
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def load_qoder_session(qdir):
    """解密 {qdir}\\auth.v1.dat，按官方 schema 校验，返回会话 dict。"""
    try:
        with open(os.path.join(qdir, QODER_LOCAL_STATE_BASENAME),
                  "r", encoding="utf-8") as f:
            local_state = json.load(f)
    except Exception as e:
        raise ValueError("读取 Local State 失败: %s" % e)

    enc_b64 = (local_state.get("os_crypt") or {}).get("encrypted_key")
    if not enc_b64:
        raise ValueError("Local State 缺少 os_crypt.encrypted_key"
                         "（目录不对，或客户端加密方案已变更）")
    enc = base64.b64decode(enc_b64)
    if enc[:5] != b"DPAPI":
        raise ValueError("encrypted_key 前缀异常（期望 b'DPAPI'），格式可能已变")

    key = _q_dpapi_unprotect(enc[5:])
    if len(key) != 32:
        raise ValueError("DPAPI 解出的密钥不是 32 字节（得到 %d）" % len(key))

    try:
        with open(os.path.join(qdir, QODER_AUTH_BASENAME), "rb") as f:
            raw = f.read()
    except Exception as e:
        raise ValueError("读取 auth.v1.dat 失败: %s" % e)
    if raw[:3] != b"v10" or len(raw) < 3 + 12 + 16:
        raise ValueError("auth.v1.dat 头部异常（期望 b'v10'），凭据格式可能已变更")

    nonce, blob = raw[3:15], raw[15:]
    plaintext = _aes256_gcm_decrypt(key, nonce, b"", blob[:-16], blob[-16:])
    try:
        auth = json.loads(plaintext.decode("utf-8"))
    except Exception as e:
        raise ValueError("auth.v1.dat 解密后不是合法 JSON（GCM 认证虽过，内容异常）: %s" % e)

    # 官方校验条件（asar 原文等价）：schemaVersion==1，四个字符串字段与
    # user.id 齐备，缺一即视为未登录。
    if (not isinstance(auth, dict) or auth.get("schemaVersion") != 1
            or not isinstance(auth.get("token"), str)
            or not isinstance(auth.get("refreshToken"), str)
            or not isinstance(auth.get("expiresAt"), str)
            or not isinstance(auth.get("user"), dict)
            or not isinstance(auth["user"].get("id"), str)):
        raise ValueError("凭据字段不完整，请在 Qoder 桌面端重新登录")

    return {
        "token": auth["token"],
        "refreshToken": auth["refreshToken"],
        "expires_at": _q_parse_time(auth.get("expiresAt")),
        "refresh_expires_at": _q_parse_time(auth.get("refreshTokenExpiresAt")),
        "email": auth["user"].get("email"),
    }


_q_base_cache = None   # 本次运行内记住先应答的主机，claim/refresh 不再重复试错


def _q_bases():
    """候选主机列表：环境变量覆盖 > 上次成功的缓存 > 内置顺序。"""
    override = os.environ.get("QODER_API_BASE")
    if override and override.strip():
        return [override.strip().rstrip("/")]
    bases = list(QODER_API_BASES)
    if _q_base_cache in bases:
        bases.remove(_q_base_cache)
        bases.insert(0, _q_base_cache)
    return bases


def build_qoder_headers(token):
    """组装 Qoder 请求头。

    官方客户端还带一串 Cosy-Version / Cosy-MachineOS / Cosy-MachineHostname 等
    设备头，但各头的取值格式没有实机依据；猜错格式反而是明显的机器指纹。
    官方 AB() 规则本就是"取到才带"，这里只带实机验证过被服务端接受的最小集。
    """
    return {
        "Accept": "application/json",
        "User-Agent": "Qoder",
        "Authorization": "Bearer %s" % token,
        "Cosy-ClientType": "10",
    }


def _qoder_request(url, headers, method="POST", payload=None, timeout=30):
    """Qoder 侧网络出口，语义与 _trae_request 完全一致（状态码, JSON体），直接复用。"""
    return _trae_request(url, headers, method=method, payload=payload, timeout=timeout)


def qoder_refresh(session):
    """用 refreshToken 换新 access token（只更新内存会话），成功返回 True。

    官方实现（asar）：POST {base}/api/v1/deviceToken/refresh，body
    {"refresh_token": ...}，不需要任何 Authorization；400/401/403 为凭证作废，
    其它非 2xx 为服务端抖动。本函数不回写 auth.v1.dat——回写需要自造 AES-GCM
    加密器，且有弄坏客户端凭据文件的风险；access token 有效期约三周，绝大
    多数运行走不到这条路径。若服务端轮换 refresh token 而我们不落盘，代价
    是下次要重新登录桌面端，两害相权取其轻。
    """
    global _q_base_cache
    if not session.get("refreshToken"):
        return False
    headers = {"Accept": "application/json",
               "Content-Type": "application/json",
               "User-Agent": "Qoder"}
    for base in _q_bases():
        code, body = _qoder_request(base + QODER_REFRESH_PATH, headers, "POST",
                                    {"refresh_token": session["refreshToken"]})
        if code == 200 and isinstance(body, dict):
            tok = body.get("device_token") or body.get("token")
            if tok:
                session["token"] = tok
                session["refreshToken"] = body.get("refresh_token") or session["refreshToken"]
                session["expires_at"] = _q_parse_time(body.get("expires_at")) or session.get("expires_at")
                _q_base_cache = base
                return True
    return False


def qoder_campaigns(session):
    """拉取活动列表。

    返回 {"ok": True, "campaigns": [...], ...}；不 ok 时 reason ∈
    {network, auth, other}。404 按官方行为视为"无活动"而不是错误；401/403
    可能是"账号体系不匹配走错主机"（实测国际 token 打 CN 主机即 401），
    所以要换完全部主机才下 auth 结论，不能见 401 就判死刑。
    """
    global _q_base_cache
    headers = build_qoder_headers(session["token"])
    auth_fail = None
    other = None
    for base in _q_bases():
        code, body = _qoder_request(base + QODER_CAMPAIGNS_PATH, headers, "GET")
        if code == 200 and isinstance(body, dict):
            _q_base_cache = base
            camps = body.get("campaigns")
            return {"ok": True, "http": 200,
                    "show_campaign": bool(body.get("showCampaign")),
                    "claimable": bool(body.get("claimable")),
                    "campaigns": camps if isinstance(camps, list) else []}
        if code == 404:
            _q_base_cache = base
            return {"ok": True, "http": 404, "show_campaign": False,
                    "claimable": False, "campaigns": []}
        if code in (401, 403):
            auth_fail = {"ok": False, "reason": "auth", "http": code, "body": body}
            continue
        if code != CODE_NO_NETWORK:
            # 主机活着但答复异常（5xx/体形不对）：换主机大概率同样，早停
            other = {"ok": False, "reason": "other", "http": code, "body": body}
            break
    if auth_fail:
        return auth_fail
    if other:
        return other
    return {"ok": False, "reason": "network", "http": CODE_NO_NETWORK,
            "body": "network unreachable"}


def _q_benefit_of(campaign):
    b = campaign.get("benefit")
    return b if isinstance(b, dict) else {}


def qoder_pick_claimable(campaigns):
    """挑出"可领积分"的活动。

    官方 iframe 前端的判定等价：actionType=CLAIM_BENEFIT、
    claimStatus=CLAIMABLE、benefit.kind=CREDITS 且 amount 为有限数。
    VIEW_DETAILS / CLAIMED / 非积分福利都不在此列。
    """
    picked = []
    for c in campaigns or []:
        if not isinstance(c, dict):
            continue
        b = _q_benefit_of(c)
        amount = b.get("amount")
        if (c.get("actionType") == "CLAIM_BENEFIT"
                and c.get("claimStatus") == "CLAIMABLE"
                and b.get("kind") == "CREDITS"
                and isinstance(amount, (int, float)) and not isinstance(amount, bool)
                and math.isfinite(amount)):
            picked.append(c)
    return picked


def qoder_claim(session, campaign_id, credits=0):
    """领取单个活动积分：POST {base}/sash/api/v1/me/campaigns/{id}/claim。

    campaignId 需 URL 编码（官方 fetch 即 encodeURIComponent + keepalive）。
    与 trae_claim 同一原则：只对"根本没拿到响应"的网络错误重试，业务错误码
    当场定论；错误响应体形如 {"errorCode", "requestId"}，原样带回便于排查。
    """
    last = None
    for attempt in range(QODER_CLAIM_RETRIES):
        base = _q_base_cache or _q_bases()[0]
        url = base + QODER_CAMPAIGNS_PATH + "/" + urllib.parse.quote(
            str(campaign_id), safe="") + "/claim"
        code, body = _qoder_request(url, build_qoder_headers(session["token"]),
                                    "POST")
        if code == CODE_NO_NETWORK and attempt + 1 < QODER_CLAIM_RETRIES:
            last = {"ok": False, "http": code, "body": body}
            time.sleep(2.0 + attempt * 2.0)
            continue
        if 200 <= code < 300:
            return {"ok": True, "http": code, "credits": credits, "body": body}
        return {"ok": False, "http": code, "body": body}
    return last or {"ok": False, "http": CODE_NO_NETWORK}


# ============================================================
# 伪 HTTP 码：区分"没拿到响应"的两种原因
CODE_NO_NETWORK = -1   # 连不上/超时
CODE_BUDGET_OUT = -2   # 本次运行的时间预算已耗尽，主动放弃后续请求

# 计划任务跑满 ExecutionTimeLimit 会被系统直接杀掉，届时 emit 还没执行，当天日志整条
# 丢失。这里自设更小的预算，确保总能走到写日志那一步。两个任务的时限不同，上限也必须
# 分开算：签到任务 PT10M、轮询任务 PT5M，各留 60s 给解释器启动和收尾。
# 这四个常量与 install-windows.ps1 里的 ExecutionTimeLimit 一一对应，改一处就要改另一处。
DEFAULT_BUDGET_SECONDS = 420.0
MAX_BUDGET_SECONDS = 540.0          # 签到任务 PT10M = 600s - 60s
# 轮询任务如今也负责补签（见 run_daily），预算比"只跑成长中心"时期宽一些，好让
# 冷启动重试跑得完；但仍远小于 PT5M，跑不完就早收尾、四小时后再来。
POLL_BUDGET_SECONDS = 180.0
POLL_MAX_BUDGET_SECONDS = 240.0     # 轮询任务 PT5M = 300s - 60s
# 每轮最多用掉几张补登卡。卡是稀缺资源（上限 4 张），而这条写路径还没被真实响应
# 验证过，一轮只花一张：猜错形状也只错一次，一天 6 轮照样能把断登补完。
MAKEUP_MAX_PER_RUN = 1
REQUEST_TIMEOUT = 30
# 网络类失败的退避节奏（秒）。定时任务最容易撞上的就是"刚开机/刚唤醒"：WiFi 重连、
# DHCP 续租、VPN 拨通往往要几十秒，而原来的策略是"5 秒后再试一次"——两次都撞在同
# 一堵墙上，420 秒预算只花掉 5 秒就判了当天死刑。退避到分钟级才真正跨得过这个窗口：
# 最多 6 次尝试摊开约 3.5 分钟，仍在签到任务的预算内。实际跑几轮由剩余预算决定
# （见 _request_with_retry 的守卫），轮询任务预算短，会自动少跑几轮。
NETWORK_RETRY_DELAYS = (5, 15, 30, 60, 90)
# 5xx 是服务端抖动，不是本机网络没就绪，短促重试即可——干等几分钟既救不了它，
# 还会把预算耗光，让后面的成长中心一个都跑不成。
SERVER_RETRY_DELAYS = (3, 10)
_started_at = None
_budget_seconds = DEFAULT_BUDGET_SECONDS
_config_warning = None   # 配置非法时的告警，由 emit 统一带进输出

# 轮询类命令：预算更短，且空跑不落盘。silent-growth 是 silent-poll 的旧名，
# 已安装的计划任务还在用它，必须继续认。
POLL_ACTIONS = ("silent-poll", "silent-growth")


def _parse_budget(default=DEFAULT_BUDGET_SECONDS, maximum=MAX_BUDGET_SECONDS):
    """解析预算环境变量。非法值/越界值一律夹到安全区间——绝不能在这里抛异常。

    这段逻辑曾写在模块顶层，WORKBUDDY_BUDGET_SECONDS=abc 会让进程在 main() 的
    try/except 生效之前就崩掉，silent 模式下当天日志整条为空。

    上限按调用方传入的 maximum 算：轮询任务的 ExecutionTimeLimit 比签到任务短，
    共用一个 540s 的上限等于对轮询任务没有上限，跑穿一样会被杀在写日志之前。
    """
    raw = os.environ.get("WORKBUDDY_BUDGET_SECONDS")
    if not raw:
        return default, None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default, "WORKBUDDY_BUDGET_SECONDS=%r 不是数字，已回落 %s 秒" % (
            raw, int(default))
    if val <= 0:
        # "0" 是非空字符串，用 `or` 兜不住；且 0 会让每个请求都直接放弃，脚本永久失效
        return default, "WORKBUDDY_BUDGET_SECONDS=%s 必须为正数，已回落 %s 秒" % (
            raw, int(default))
    if val > maximum:
        # 上限同样是硬要求：预算大于任务时限就等于没有预算，黑洞式超时会把进程跑到被强杀
        return maximum, ("WORKBUDDY_BUDGET_SECONDS=%s 超过本命令上限，已夹到 %s 秒"
                         "（须小于该定时任务的 ExecutionTimeLimit）") % (
            raw, int(maximum))
    return val, None


def _start_budget(action=None):
    """启动预算时钟；配置非法时记下告警，由 emit 带进输出而不是静默回落。

    轮询类命令（silent-poll / silent-growth）用更短的默认值和更低的上限，用户显式设置
    环境变量时仍以环境变量为准，但同样夹在该命令自己的上限内。
    """
    global _started_at, _budget_seconds, _config_warning
    if action in POLL_ACTIONS:
        default, maximum = POLL_BUDGET_SECONDS, POLL_MAX_BUDGET_SECONDS
    else:
        default, maximum = DEFAULT_BUDGET_SECONDS, MAX_BUDGET_SECONDS
    _budget_seconds, _config_warning = _parse_budget(default, maximum)
    _started_at = time.monotonic()


def _budget_left():
    """本次运行还剩多少秒可用于网络请求。"""
    if _started_at is None:
        return _budget_seconds
    return _budget_seconds - (time.monotonic() - _started_at)


def find_auth_file():
    """按平台探测 WorkBuddy 桌面端写出的登录凭据文件，支持环境变量覆盖。

    返回 (path_or_None, looked_in) —— looked_in 始终是脚本实际检查过的路径列表，
    供 NO_AUTH 报错时显示，避免与代码实现漂移。
    """
    override = os.environ.get("WORKBUDDY_AUTH_FILE")
    if override:
        # 环境变量优先，但文件不存在时单独报告，不给无关建议
        return (override if os.path.exists(override) else None), [override]
    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    xdg_data = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    candidates = [
        os.path.join(local, AUTH_BASENAME),                                  # Windows 桌面端
        os.path.join(home, "Library", "Application Support", AUTH_BASENAME),  # macOS 桌面端
        os.path.join(xdg_data, CLI_AUTH_BASENAME),                           # Linux CodeBuddy CLI
        os.path.join(home, ".config", AUTH_BASENAME),                        # Linux（旧猜测，保留）
        os.path.join(home, ".workbuddy", "auth", "workbuddy-desktop.info"),  # 兜底
    ]
    for c in candidates:
        if os.path.exists(c):
            return c, candidates
    return None, candidates


def load_session(auth_file):
    """读凭据并解封加密字段。旧格式（明文）与新版（$wbEncrypted 信封）都支持。"""
    with open(auth_file, "r", encoding="utf-8") as f:
        session = json.load(f)
    return _wb_unseal_session(session)


# ============================================================
# 客户端"是否安装"探测（供 web 控制台做"装客户端 + 有凭据"就绪判定）
# ============================================================
def is_product_installed(appdata_names, exec_names=(), extra_dirs=()):
    """粗略判断本机是否装过某产品，返回 (found_path_or_None, looked_in)。

    判据（命中任一条即视为已安装）：
      1. 用户级应用数据目录存在（%APPDATA% / %LOCALAPPDATA% 下的产品目录）；
      2. 常见安装目录里出现主程序 exe（%LOCALAPPDATA%\\Programs、%ProgramFiles%、
         %ProgramFiles(x86)%）。
    这是尽力而为的探测，返回的 found_path 只是"最先命中"的一条路径；都不命中时
    返回 (None, looked_in)，好让上层把真正查过的地方展示给用户。
    """
    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    appdata = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
    pf = os.environ.get("ProgramFiles") or r"C:\Program Files"
    pf86 = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
    programs = os.path.join(local, "Programs")

    candidates = []
    for n in appdata_names:
        candidates.append(os.path.join(appdata, n))
        candidates.append(os.path.join(local, n))
    for exe in exec_names:
        for base in (programs, pf, pf86):
            candidates.append(os.path.join(base, exe))
    for d in extra_dirs:
        candidates.append(d)

    for c in candidates:
        if os.path.exists(c):
            return c, candidates
    return None, candidates


def load_session_retry(auth_file, attempts=3, delay=2.0):
    """带重试的凭据读取。

    WorkBuddy 客户端刷新 token 时会短暂独占凭据文件，恰好在那一刻读取就是
    PermissionError（实测：定时任务两次撞锁、整轮直接放弃）。锁是瞬时的，
    等两秒再试即可；重试仍失败才真正报错。
    """
    last = None
    for i in range(attempts):
        try:
            return load_session(auth_file)
        except PermissionError as e:
            last = e
            if i < attempts - 1:
                time.sleep(delay)
    raise last


def build_headers(session):
    auth = session.get("auth") or {}
    account = session.get("account") or {}
    token = auth.get("accessToken")
    uid = account.get("uid")
    # 桌面端把凭据换成加密信封后，旧代码会把整个信封字典拼进 "Bearer %s"，实际发出去的
    # 是一段字典字面量，服务端只会回 401，报错却指向"登录失效"——方向完全错。这里把
    # 类型问题就地拦下，说清楚到底是哪种异常。
    if _wb_is_envelope(token):
        raise EncryptedAuthError("accessToken 仍是加密信封（未解封）")
    if not token or not uid:
        raise ValueError("NO_SESSION: 本地未找到有效登录会话")
    if not isinstance(token, str):
        raise ValueError("NO_SESSION: accessToken 类型异常（%s）" % type(token).__name__)
    if not isinstance(uid, (str, int)):
        raise ValueError("NO_SESSION: 用户 uid 类型异常（%s）" % type(uid).__name__)
    headers = {
        "Accept": "application/json",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Authorization": "Bearer %s" % token,
        "Content-Type": "application/json",
        "X-User-Id": uid,
        "User-Agent": _electron_ua("WorkBuddy"),
    }
    if account.get("enterpriseId"):
        headers["X-Enterprise-Id"] = account["enterpriseId"]
        headers["X-Tenant-Id"] = account["enterpriseId"]
    if auth.get("domain"):
        headers["X-Domain"] = auth["domain"]
    return headers


def _request(url, headers, method="GET", payload=None, timeout=REQUEST_TIMEOUT):
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, {"raw": raw[:500]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:500]}
    except urllib.error.URLError as e:
        return CODE_NO_NETWORK, {"error": str(e.reason)}
    except Exception as e:
        return CODE_NO_NETWORK, {"error": str(e)}


def post(url, headers, payload=None, retry=False):
    """POST 默认不重试：抽奖/领奖等写操作若在服务端处理完成后才超时，重试会重复提交。"""
    return _request_with_retry(url, headers, method="POST", payload=payload, retry=retry)


def get(url, headers):
    return _request_with_retry(url, headers, method="GET", retry=True)


def _retry_delays(code):
    """该失败码对应的退避节奏；空元组表示"重试也没用"，立刻如实返回。

    只有这两类值得再试：本机网络没就绪（-1）、服务端抖动（5xx）。
    4xx 是业务规则或参数问题，重试一百次也是同一个答案；CODE_BUDGET_OUT 更是
    连请求都没发出去，再试只会离被强杀更近一步。
    """
    if code == CODE_NO_NETWORK:
        return NETWORK_RETRY_DELAYS
    if code >= 500:
        return SERVER_RETRY_DELAYS
    return ()


def _request_with_retry(url, headers, method="GET", payload=None, retry=True):
    """带时间预算的请求：失败按退避节奏重试，超时上限随剩余预算收缩，预算不足则直接放弃。

    返回 CODE_BUDGET_OUT 表示"没发出去，因为再发就要超出任务时限了"——调用方据此提前
    收尾，保证 emit 一定能执行到。

    退避节奏见 NETWORK_RETRY_DELAYS：早期版本固定"重试 1 次、隔 5 秒"，对定时任务
    最常见的失败场景（刚开机/唤醒，网络还要几十秒才就绪）几乎是无效重试。
    """
    if _budget_left() <= 1:
        return CODE_BUDGET_OUT, {"error": "已达本次运行时间预算，跳过剩余请求"}

    code, body = _request(url, headers, method=method, payload=payload,
                          timeout=max(1, min(REQUEST_TIMEOUT, _budget_left())))
    if not retry:
        return code, body

    # 退避进度按"失败类型"各记一份，而不是一个全局计数。
    # 失败类型会在重试途中变化：典型的是冷启动——前几次网络不可达（网卡刚连上），
    # 之后转成 500（代理还没就绪）。全局计数会让这种 5xx 撞上已经用光的计数、
    # 一次都重试不到（网络类已推进到 2，而 5xx 的节奏只有 2 项）。按节奏分桶后，
    # 每类各自从头走自己的退避表。总尝试次数仍有界：至多 len(两类节奏之和)+1 次。
    attempts = {}
    while True:
        delays = _retry_delays(code)
        if not delays:
            return code, body
        used = attempts.get(delays, 0)
        if used >= len(delays):
            return code, body
        delay = delays[used]
        attempts[delays] = used + 1
        # 一轮重试最坏要占掉 delay + 一整个超时，预算不够就别开始：宁可现在如实返回
        # 失败，也不能跑穿任务时限被系统强杀——那样连日志都写不出来，当天记录整条丢失。
        if _budget_left() <= delay + REQUEST_TIMEOUT:
            return code, body
        time.sleep(delay)
        code, body = _request(url, headers, method=method, payload=payload,
                              timeout=max(1, min(REQUEST_TIMEOUT, _budget_left())))


def _is_hard_failure(code):
    """是否属于"需要人关注"的失败。

    5xx 与"没拿到响应"算硬失败；4xx 绝大多数是业务规则（如派 Buddy 已达每日上限、
    活动已结束），属于每天的正常状态，若计入退出码会让计划任务天天报红。
    """
    return code >= 500 or code in (CODE_NO_NETWORK, CODE_BUDGET_OUT)


def _http_label(code):
    """把伪 HTTP 码翻译成人话；-1/-2 是脚本自定义的"没拿到响应"标记。"""
    if code == CODE_NO_NETWORK:
        return "网络不可达"
    if code == CODE_BUDGET_OUT:
        return "时间预算耗尽"
    return "HTTP %s" % code


def _is_no_chance(msg):
    """抽奖失败是否只是"没有次数"——这是常态，不是故障。

    服务端对"次数为 0"返回 400 + `insufficient lottery chance balance`，和真正的
    参数错误（`invalid request`）同为 400，只看状态码会把两者混为一谈：把常态记成
    失败，轮询就会每轮强制落盘、还会把失败数算进汇报。
    """
    m = str(msg or "").lower()
    if not m:
        return False
    if "insufficient" in m or "not enough" in m:
        return "chance" in m or "balance" in m
    return "no chance" in m


def _is_unknown_tier(code, body):
    """连登兑换是否因为"tier 这个值本身不认识"被拒——用于判断要不要换一种写法重试。

    /redeem 的 tier 是**档位标识字符串**（"7d"/"14d"/"28d"），权威来源是
    GET /streak 的 redemption_status.tiers[].tier。接口哪天改回收天数，脚本就会
    三档全废且看不出原因，所以保留这条兜底：档位标识被判 unknown tier 时退回天数
    再试一次。这类 400 发生在参数校验阶段，服务端没兑换任何东西，重试不会重复领取；
    `invalid request` 这类业务拒绝不在此列，不能重试。

    历史教训：2026-09-11 曾据"传天数返回 invalid request"误判 tier 要收数字，
    导致兑换模块连坏三个版本——"业务拒绝"的错误文案不能反推参数格式正确，
    必须拿到一次成功响应才算验证过。
    """
    if code != 400:
        return False
    m = str(dig(body, "msg") or "").lower()
    return "tier" in m and ("unknown" in m or "unsupported" in m or "invalid" in m)


def _is_tier_locked(code, body):
    """未解锁档位：403 + 「连续登录天数不足」——这是常态，不是故障。

    不加这条的话，未解锁档位会被计进 failures，轮询每轮都判定"有失败"从而强制
    落盘，真正有价值的记录会被这类常态信息淹没。
    """
    if code != 403:
        return False
    m = str(dig(body, "msg") or "")
    return "天数不足" in m or "不足" in m


def dig(obj, key):
    """在可能被 data/result 包裹的响应里找字段，兼容信封结构。"""
    if isinstance(obj, dict):
        if key in obj and obj[key] is not None:
            return obj[key]
        for k in ("data", "result", "resp", "response"):
            if k in obj and isinstance(obj[k], dict):
                r = dig(obj[k], key)
                if r is not None:
                    return r
    return None


def fmt_credit(v):
    """积分显示用：能转 int 就转，否则原样返回（OverflowError 同理，见 as_int）。"""
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return v


def as_int(v, default=0):
    """把可能为字符串的数字安全地转成 int，避免与数值比较/累加时抛异常。

    OverflowError 必须一并捕获：json.loads 默认接受 Infinity，服务端返回该字面量时
    v 已经是 float('inf')，int(v) 抛的是 OverflowError 而非 ValueError。
    """
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        return int(float(v))  # 兼容 "1.5"/"1e3" 这类数字串，宁可截断也不把真实数值丢成 0
    except (TypeError, ValueError, OverflowError):
        return default


def _first_int(body, key, fallback=0):
    """优先取响应里的实际数值，挖不到才用回落值。

    任务列表里的 reward_credit 只是活动配置，与服务端这次实际发放的可能不同；
    上报按响应值才不会虚报，响应里没有时才退回配置值。
    """
    if isinstance(body, dict):
        v = dig(body, key)
        if v is not None:
            return as_int(v)
    return as_int(fallback)


def _fmt_eta(arrive_at, server_now):
    """把服务端返回的 Unix 时间戳换算成"还有多久回来"。

    任一时间戳缺失/非法就返回空串——这是纯展示信息，绝不能因为它让整轮执行失败。
    json.loads 默认接受 Infinity/NaN，非有限值同样按缺失处理，否则会汇报出
    "约 inf 小时后回"这种话。
    """
    try:
        left = float(arrive_at) - float(server_now)
    except (TypeError, ValueError, OverflowError):
        return ""
    if not math.isfinite(left):
        return ""
    if left <= 0:
        return "，已到达待领取"
    # 先算分钟再决定用哪个量纲：直接按 left < 3600 分档会让 3599s 显示成"约 60 分钟"
    minutes = int(round(left / 60.0))
    if minutes < 60:
        return "，约 %d 分钟后回" % max(1, minutes)
    return "，约 %.1f 小时后回" % (left / 3600.0)


def _env_flag(name):
    """开关型环境变量是否为真；空串与 0/false/no/off 一律视为关。"""
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _client_token(prefix="u"):
    """活动接口（抽奖/连登兑换）要求的防重放 token。

    官方前端用 `crypto.randomUUID()` 拼成 "u-<uuid>"，服务端只做幂等去重、
    不校验格式；缺了它 /lottery/draw 直接 400（实测过）。
    """
    return "%s-%s" % (prefix, uuid.uuid4())


# 连登兑换各档奖励的官方文案（2026-09 活动版本）。兑换成功的汇报优先用
# 服务端返回的实际明细，挖不到字段时才回落到这里——活动改版时以响应为准。
_REDEEM_REWARDS = {
    "7d":  "+2 能量 +1 补登卡 +1 次抽奖",
    "14d": "+50 积分 +3 能量 +1 补登卡 +1 次抽奖",
    "28d": "+150 积分 +5 能量 +1 补登卡 +1 次抽奖",
}

# 连登兑换三档：(档位标识, /redeem/summary 的状态字段前缀, 展示名, 天数)
# tier 的口径是档位标识字符串，天数只用于兜底重试时退回旧写法。
_REDEEM_TIERS = (
    ("7d",  "starter",   "入门", 7),
    ("14d", "advanced",  "进阶", 14),
    ("28d", "legendary", "巅峰", 28),
)


def _redeem_reward_desc(body, tier):
    """兑换成功的奖励描述：优先拼服务端实发的 *_granted，挖不到才回落官方文案。

    注意实发字段名带 _granted 后缀（credit_granted / energy_granted /
    cards_granted / chances_granted）；直接读 `credit` 恒为空，会把兑换所得漏计。
    """
    bits = []
    credit = as_int(dig(body, "credit_granted"), 0)
    energy = as_int(dig(body, "energy_granted"), 0)
    cards = as_int(dig(body, "cards_granted"), 0)
    chances = as_int(dig(body, "chances_granted"), 0)
    if credit:
        bits.append("+%s 积分" % fmt_credit(credit))
    if energy:
        bits.append("+%s 能量" % fmt_credit(energy))
    if cards:
        bits.append("+%s 补登卡" % fmt_credit(cards))
    if chances:
        bits.append("+%s 次抽奖" % fmt_credit(chances))
    if bits:
        return "（%s）" % " ".join(bits)
    return "（%s）" % _REDEEM_REWARDS.get(tier, "奖励已到账")


def _dumps(out):
    """序列化汇报内容；default=str 兜住意外混入的非 JSON 类型，绝不让唯一的输出通道崩掉。"""
    try:
        return json.dumps(out, ensure_ascii=False, default=str)
    except Exception:
        return repr(out)


def emit(out, action):
    """silent 类模式写日志文件，其余模式打印到 stdout；本函数保证不抛异常。

    判定用前缀而不是等号：silent-poll / silent-growth 同样是无窗口跑的，pythonw 下
    sys.stdout 是 None，print 会静默成功（不抛异常），于是"打到 stdout"等于扔进黑洞——
    NO_AUTH、NO_SESSION 这类最需要人处理的结果会一条不剩地消失。

    ERROR 两条通道都走：计划任务里若把命令名写错（silent 拼成 slient 之类），
    action 不带 silent 前缀，走 stdout 就等于扔进黑洞，无窗口运行下这次失败再没有
    任何痕迹——正是本脚本想杜绝的"当天日志整条丢失"。
    """
    if _config_warning and isinstance(out, dict):
        out = dict(out, config_warning=_config_warning)
    payload = _dumps(out)
    is_error = isinstance(out, dict) and out.get("result") == "ERROR"
    if not str(action).startswith("silent"):
        try:
            print(payload)
            if not is_error:
                return
        except Exception:
            pass  # stdout 不可用（编码/管道问题）时退到日志，至少不把结果丢掉

    line = "[%s] %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), payload)
    default_log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signin.log")
    # 自定义路径不可写时回退到脚本同目录，避免无窗口运行下结果彻底丢失
    for path in (os.environ.get("WORKBUDDY_SIGNIN_LOG") or default_log, default_log):
        try:
            with open(path, "a", encoding="utf-8") as lf:
                lf.write(line)
            return
        except Exception:
            continue


def _is_already_checked_in(cbody):
    """领取接口返回是否表示"今日已签"（兼容 null 与 400+code10001）。"""
    if cbody is None:
        return True
    if isinstance(cbody, dict):
        msg = cbody.get("msg") or ""
        if cbody.get("code") == 10001 or "已签" in msg:
            return True
    return False


def _already_report(status, via=None):
    """根据状态构造"今日已签"汇报 dict。"""
    today_credit = dig(status, "today_credit") or dig(status, "daily_credit")
    streak_days = dig(status, "streak_days")
    total_credits = dig(status, "total_credits")
    is_streak_day = dig(status, "is_streak_day")
    next_streak_day = dig(status, "next_streak_day")
    inner = []
    if today_credit is not None:
        inner.append("今日 +%s" % fmt_credit(today_credit))
    if streak_days is not None:
        inner.append("连续 %s 天" % streak_days)
    if total_credits is not None:
        inner.append("累计 %s 积分" % fmt_credit(total_credits))
    prefix = via or "今日已签过"
    report = "%s（%s）" % (prefix, "，".join(inner)) if inner else prefix
    return {
        "result": "ALREADY",
        "report": report,
        "today_credit": today_credit,
        "streak_days": streak_days,
        "total_credits": total_credits,
        "is_streak_day": is_streak_day,
        "next_streak_day": next_streak_day,
    }


def run_growth(headers, endpoint):
    """成长中心自动化：领旅行礼物→派 Buddy→领任务/领取新任务→补登→连登兑换→开盲盒→能量开 Buddy→汇报。

    各子步骤单独 try，一段失败不影响其余领取；任一步遇 401/403 直接升级为 NO_SESSION。
    """
    base = endpoint + "/v2/activity/growth"
    parts = []
    credits_gained = 0
    failures = 0        # 全部失败项，仅用于汇报
    hard_failures = 0   # 其中"需要关注"的那些，只有它们影响退出码
    successes = 0

    def _check_auth(code):
        """返回 True 表示需要立即退出（登录态失效）。"""
        return code in (401, 403)

    def _note_http(code, body, label):
        """前置查询接口非 2xx 时的统一记录；返回 True 表示调用方应跳过后续处理。

        硬失败（5xx / 网络不可达 / 预算耗尽）必须计入 failures：否则整轮
        successes=0 且 failures=0 会被判成 idle，轮询既不写日志又返回 0，
        服务端故障彻底无声无息。旅行模块早就这么做了，其余各段没跟上。

        4xx 只进报告、不计失败：绝大多数是业务规则（活动未开始、接口下线），
        计入会让轮询天天强制落盘。手动跑 `growth` 仍能在报告里看到它。
        """
        nonlocal failures, hard_failures
        if 200 <= code < 300:
            return False
        reason = _http_label(code)
        detail = ""
        if isinstance(body, dict):
            detail = str(body.get("error") or body.get("msg") or "")
        # 状态码与服务端给的详情都保留：只知道 500 无法判断影响，只知道 "timed out"
        # 又看不出是网络还是服务端，轮询日志里这两者都想要
        parts.append("%s失败：%s" % (label, "%s（%s）" % (reason, detail)
                                     if detail and detail != reason else (detail or reason)))
        if _is_hard_failure(code):
            failures += 1
            hard_failures += 1
        return True

    # --- 1. Buddy 旅行：领礼物 + 派出发 ---
    try:
        scode, sbody = get(base + "/buddy/travel/status", headers)
        if scode == CODE_BUDGET_OUT:
            return 1, {"result": "TIMEOUT",
                       "report": "时间预算耗尽，成长中心跳过，下次自动重试"}
        if scode == CODE_NO_NETWORK:
            # 网络不可达就立刻收手，别把后续 5 个接口的重试+等待全跑一遍
            return 1, {"result": "NETWORK",
                       "report": "网络不可达，成长中心跳过（%s）" % (sbody.get("error") or "")}
        if _check_auth(scode):
            return 1, {"result": "NO_SESSION",
                       "report": "登录态已失效，请重新登录 WorkBuddy 桌面端"}
        travel = dig(sbody, "state") if (200 <= scode < 300) else None
        # 服务端明确给出"今日旅行名额已用完"。读它而不是等 depart 报错，
        # 轮询场景下差别很大：后者会让每一轮都白撞一次墙。
        daily_limit = bool(dig(sbody, "daily_limit_reached")) if (200 <= scode < 300) else False
        _note_http(scode, sbody, "查旅行状态")
        claimed_travel = False
        if travel == "arrived":
            record_id = dig(sbody, "record_id")
            ccode, cbody = post(base + "/buddy/travel/claim", headers, {"record_id": record_id})
            if _check_auth(ccode):
                return 1, {"result": "NO_SESSION",
                           "report": "登录态已失效，请重新登录 WorkBuddy 桌面端"}
            if 200 <= ccode < 300 and dig(cbody, "reward_credit") is not None:
                got = as_int(dig(cbody, "reward_credit"))
                credits_gained += got
                parts.append("领旅行礼物 +%s 积分" % fmt_credit(got))
                successes += 1
                claimed_travel = True
            else:
                # 领失败：带出业务 msg，不再说"HTTP 200"；也不派 Buddy 出发，避免覆盖未领取的奖励
                msg = (dig(cbody, "msg") or "") if isinstance(cbody, dict) else ""
                parts.append("领旅行礼物失败：%s" % (msg or "HTTP %s" % ccode))
                failures += 1
                hard_failures += _is_hard_failure(ccode)
            if claimed_travel:
                travel = "idle"  # 只有领取成功后才视为 idle，允许派出发
        if travel == "idle" and daily_limit:
            # 今日名额已用完：直接收手，不碰 config/depart，省掉两个请求和一条必然的失败
            parts.append("今日旅行名额已用完")
        elif travel == "idle":
            ccode, cbody = get(base + "/buddy/travel/config", headers)
            if _check_auth(ccode):
                return 1, {"result": "NO_SESSION",
                           "report": "登录态已失效，请重新登录 WorkBuddy 桌面端"}
            locs = dig(cbody, "locations") if (200 <= ccode < 300) else None
            if locs and isinstance(locs[0], dict):
                loc = locs[0]
                dcode, dbody = post(base + "/buddy/travel/depart", headers,
                                    {"location_id": loc.get("id")})
                if _check_auth(dcode):
                    return 1, {"result": "NO_SESSION",
                               "report": "登录态已失效，请重新登录 WorkBuddy 桌面端"}
                if 200 <= dcode < 300:
                    loc_name = (dig(dbody, "location") or {}).get("name", "?")
                    dur = dig(dbody, "duration_hours") or (dig(dbody, "location") or {}).get("duration_hours", "?")
                    parts.append("派 Buddy 去%s（%s 小时后回）" % (loc_name, dur))
                    successes += 1
                else:
                    msg = dig(dbody, "msg") or ""
                    parts.append("派 Buddy 失败：%s" % (msg or "HTTP %s" % dcode))
                    failures += 1
                    hard_failures += _is_hard_failure(dcode)
        elif travel == "traveling":
            loc_name = (dig(sbody, "location") or {}).get("name", "?")
            # arrive_at / server_now 是服务端时间戳，比本地时钟可靠
            parts.append("Buddy 旅行中（%s%s）" % (
                loc_name, _fmt_eta(dig(sbody, "arrive_at"), dig(sbody, "server_now"))))
    except Exception as e:
        parts.append("旅行模块异常（%s: %s）" % (type(e).__name__, e))
        failures += 1
        hard_failures += 1

    # --- 2. 任务领奖（放在抽奖前：任务送的抽奖机会/能量，后面马上能用上）---
    if _budget_left() <= 0:
        parts.append("时间预算耗尽，任务领奖跳过")
    else:
        try:
            tcode, tbody = get(base + "/tasks", headers)
            if _check_auth(tcode):
                return 1, {"result": "NO_SESSION",
                           "report": "登录态已失效，请重新登录 WorkBuddy 桌面端"}
            if not _note_http(tcode, tbody, "查任务列表"):
                tasks = dig(tbody, "tasks") or []
                # 真实契约（2026-09 从桌面端成长中心 H5 的 growthSpace chunk 读出）：
                #   accept_status: not_accepted | accepted | in_progress | completed | claimed
                #   接单 POST /tasks/accept  body {"task_codes": [code, ...]}   ← 复数数组
                #        （旧的单数 {"task_code": x} 在新服务端一律 400 invalid request）
                #   领奖 POST /tasks/{task_code}/claim   ← 路径带 code、body 空
                #        （旧版拿 /tasks/accept 当领奖用，同样 400）
                titles = {t.get("task_code"): t.get("title", t.get("task_code")) for t in tasks}
                pending = [t.get("task_code") for t in tasks
                           if t.get("task_code") and not t.get("locked")
                           and t.get("accept_status") == "not_accepted"]
                for i in range(0, len(pending), 20):   # 分批，别把 body 撑大
                    if _budget_left() <= 0:
                        parts.append("时间预算耗尽，剩余任务下次再接单")
                        break
                    batch = pending[i:i + 20]
                    acode, abody = post(base + "/tasks/accept", headers,
                                        {"task_codes": batch})
                    if _check_auth(acode):
                        return 1, {"result": "NO_SESSION",
                                   "report": "登录态已失效，请重新登录 WorkBuddy 桌面端"}
                    # 逐条读 results：接单失败必须报出来（常见
                    # "prerequisite not met: first_buddy (no buddy instance found)"），
                    # 静默吞掉的话接口坏了也没人知道——轮询空跑是不写日志的。
                    results = dig(abody, "results")
                    if not isinstance(results, list):
                        results = [{"task_code": c,
                                    "status": "ok" if 200 <= acode < 300 else "error",
                                    "message": dig(abody, "msg")} for c in batch]
                    for r in results:
                        title = titles.get(r.get("task_code"), r.get("task_code"))
                        if r.get("status") == "error":
                            parts.append("领取任务「%s」失败：%s" % (
                                title, r.get("message") or "HTTP %s" % acode))
                            failures += 1
                            hard_failures += _is_hard_failure(acode)
                        else:
                            parts.append("领取任务「%s」（进度开始计）" % title)
                            successes += 1
                for t in tasks:
                    if _budget_left() <= 0:
                        parts.append("时间预算耗尽，剩余任务奖下次再领")
                        break
                    if t.get("locked") or t.get("accept_status") != "completed":
                        continue   # 只有 completed 才发奖，且走独立路径
                    code = t.get("task_code")
                    title = titles.get(code, code)
                    try:
                        ccode, cbody = post(base + "/tasks/%s/claim" % code, headers, {})
                        if _check_auth(ccode):
                            return 1, {"result": "NO_SESSION",
                                       "report": "登录态已失效，请重新登录 WorkBuddy 桌面端"}
                        if 200 <= ccode < 300 and not dig(cbody, "already_claimed"):
                            # 优先用服务端实发数值；列表里的 reward_credit 只是活动配置，
                            # 改版时会和实发对不上，挖不到才回落到列表值。
                            rc = _first_int(cbody, "credit", t.get("reward_credit"))
                            re_ = _first_int(cbody, "energy", t.get("reward_energy"))
                            credits_gained += rc
                            parts.append("领任务奖「%s」+credit%s+energy%s" % (title, rc, re_))
                            successes += 1
                        elif 200 <= ccode < 300:
                            parts.append("任务奖「%s」已领过" % title)
                        else:
                            parts.append("领任务奖「%s」失败：%s" % (
                                title, dig(cbody, "msg") or "HTTP %s" % ccode))
                            failures += 1
                            hard_failures += _is_hard_failure(ccode)
                    except Exception as e:
                        parts.append("领任务奖「%s」异常（%s: %s）" % (
                            code, type(e).__name__, e))
                        failures += 1
                        hard_failures += 1
        except Exception as e:
            parts.append("任务模块异常（%s: %s）" % (type(e).__name__, e))
            failures += 1
            hard_failures += 1

    # --- 3. 补登卡：断登自动补一张，保住连登 ---
    # 官方规则：补登卡上限 4 张、仅可补救当月断登；/streak 的 makeup_dates
    # 是服务端算好的可补日期。卡攒着不花，超上限后新卡也拿不到，断登优先补。
    # 放在连登兑换之前：补登会改变连登天数，先补，兑换才能拿到最新解锁状态。
    # 注意：实测时 makeup_dates 一直是 []，这条写路径没有被真实响应验证过，
    # 所以每轮最多补一张（见 MAKEUP_MAX_PER_RUN），万一形状猜错也只错一次。
    streak_body = None    # 复用给第 7 段的展示值，避免同一轮打两次 /streak
    streak_stale = False  # 补登成功会改变连签天数，此时必须重新取
    if _budget_left() <= 0:
        parts.append("时间预算耗尽，补登跳过")
    else:
        try:
            mcode, mbody = get(base + "/streak", headers)
            if _check_auth(mcode):
                return 1, {"result": "NO_SESSION",
                           "report": "登录态已失效，请重新登录 WorkBuddy 桌面端"}
            if not _note_http(mcode, mbody, "查连登状态"):
                streak_body = mbody
                # 余额兼容两种形状：{"makeup_cards":{"balance":2}} 与 {"makeup_cards":2}。
                # 只认前者的话，接口是后者时整个补登会一声不响地永不执行。
                cards_obj = dig(mbody, "makeup_cards")
                cards = as_int(cards_obj.get("balance")) if isinstance(cards_obj, dict) \
                    else as_int(cards_obj)
                # 实测 makeup_dates 在 streak 对象内部（不在顶层），当前值为 []；
                # dig 只做顶层查找，这里手动下钻，两处都兜住以防接口调整。
                streak_obj = dig(mbody, "streak") or {}
                dates = (streak_obj.get("makeup_dates") if isinstance(streak_obj, dict) else None) \
                    or dig(mbody, "makeup_dates") or []
                if cards > 0 and isinstance(dates, list) and dates:
                    for d in dates[:min(cards, MAKEUP_MAX_PER_RUN)]:
                        if _budget_left() <= 0:
                            parts.append("时间预算耗尽，剩余补登下次再做")
                            break
                        ucode, ubody = post(base + "/makeup-cards/use", headers,
                                            {"target_date": d, "client_token": _client_token()})
                        if _check_auth(ucode):
                            return 1, {"result": "NO_SESSION",
                                       "report": "登录态已失效，请重新登录 WorkBuddy 桌面端"}
                        if 200 <= ucode < 300:
                            cards -= 1
                            streak_stale = True
                            # 优先报服务端给的余额，本地递减只是接口没给时的兜底
                            left_obj = dig(ubody, "makeup_cards")
                            left_cards = as_int(left_obj.get("balance"), cards) \
                                if isinstance(left_obj, dict) else as_int(left_obj, cards)
                            parts.append("补登 %s（剩 %s 张卡）" % (d, left_cards))
                            successes += 1
                        else:
                            msg = dig(ubody, "msg") or ""
                            parts.append("补登 %s 失败：%s" % (d, msg or "HTTP %s" % ucode))
                            failures += 1
                            hard_failures += _is_hard_failure(ucode)
                    if len(dates) > MAKEUP_MAX_PER_RUN and cards > 0:
                        parts.append("另有 %s 天可补、剩 %s 张卡，下轮继续" % (
                            len(dates) - MAKEUP_MAX_PER_RUN, cards))
        except Exception as e:
            parts.append("补登模块异常（%s: %s）" % (type(e).__name__, e))
            failures += 1
            hard_failures += 1

    # --- 4. 连登奖励兑换（入门/进阶/巅峰三档，附积分/能量/补登卡/抽奖机会）---
    # 入门 7 天、进阶 14 天、巅峰 28 天解锁。summary 只报 claimed/locked 两种
    # 已见状态；"非 claimed 且非 locked"即视为可兑换去尝试，被服务端拒绝时按
    # 普通业务失败处理（不算硬失败）。
    if _budget_left() <= 0:
        parts.append("时间预算耗尽，连登兑换跳过")
    else:
        try:
            rcode, rbody = get(base + "/redeem/summary", headers)
            if _check_auth(rcode):
                return 1, {"result": "NO_SESSION",
                           "report": "登录态已失效，请重新登录 WorkBuddy 桌面端"}
            if not _note_http(rcode, rbody, "查连登兑换"):
                # tier 传**档位标识**（"7d"/"14d"/"28d"），不是天数也不是档位名：
                # 实测传 "starter"/"7"/7 分别得到 unknown tier / unknown tier /
                # invalid request，只有 "7d" 会 200 兑换成功。权威来源是 GET /streak
                # 的 redemption_status.tiers[].tier；状态字段仍读 /redeem/summary
                # 的 starter/advanced/legendary_status，两者一一对应。
                for tier, status_key, label, days in _REDEEM_TIERS:
                    if _budget_left() <= 0:
                        parts.append("时间预算耗尽，剩余连登兑换下次再领")
                        break
                    status = dig(rbody, status_key + "_status")
                    # 字段缺失（None）同样跳过：接口改版时不该让脚本对三档无脑 POST。
                    # 注意别在这里再加 *_count 之类的"双保险"：实测响应里
                    # starter_count=1 与 total_consumed=0 并存，count 到底是
                    # "已兑换次数"还是"可兑换次数"并不确定，猜错就会把整段静默关掉。
                    if not status or status in ("claimed", "locked"):
                        continue
                    c2code, c2body = post(base + "/redeem", headers,
                                          {"tier": tier, "client_token": _client_token()})
                    # 档位标识被判为未知时退回天数再试一次：这类 400 是参数校验阶段
                    # 的拒绝，服务端没兑换任何东西，重试不会重复领取
                    if _is_unknown_tier(c2code, c2body):
                        c2code, c2body = post(base + "/redeem", headers,
                                              {"tier": days, "client_token": _client_token()})
                    # 403「连登天数不足」是业务常态，必须**先于** _check_auth 判断：
                    # _check_auth 把 401/403 一律视为登录失效，若让它先跑，未解锁档位
                    # 会被误报成"登录态已失效"并直接中止整个成长中心。
                    if _is_tier_locked(c2code, c2body):
                        parts.append("连登兑换「%s」未解锁（连登天数不足）" % label)
                        continue
                    if _check_auth(c2code):
                        return 1, {"result": "NO_SESSION",
                                   "report": "登录态已失效，请重新登录 WorkBuddy 桌面端"}
                    if 200 <= c2code < 300:
                        credits_gained += as_int(dig(c2body, "credit_granted"))
                        parts.append("连登兑换「%s」%s" % (label, _redeem_reward_desc(c2body, tier)))
                        successes += 1
                    else:
                        msg = dig(c2body, "msg") or ""
                        parts.append("连登兑换「%s」失败：%s" % (label, msg or "HTTP %s" % c2code))
                        failures += 1
                        hard_failures += _is_hard_failure(c2code)
        except Exception as e:
            parts.append("连登兑换模块异常（%s: %s）" % (type(e).__name__, e))
            failures += 1
            hard_failures += 1

    # --- 5. 盲盒/抽奖（draw 必须带 client_token，缺了会 400）---
    if _budget_left() <= 0:
        parts.append("时间预算耗尽，盲盒跳过")
    else:
        try:
            lcode, lbody = get(base + "/lottery/chances", headers)
            if _check_auth(lcode):
                return 1, {"result": "NO_SESSION",
                           "report": "登录态已失效，请重新登录 WorkBuddy 桌面端"}
            chances = 0 if _note_http(lcode, lbody, "查抽奖机会") else as_int(dig(lbody, "balance"))
            if chances > 0:
                dcode, dbody = post(base + "/lottery/draw", headers,
                                    {"client_token": _client_token()})
                if _check_auth(dcode):
                    return 1, {"result": "NO_SESSION",
                               "report": "登录态已失效，请重新登录 WorkBuddy 桌面端"}
                if 200 <= dcode < 300:
                    prize = dig(dbody, "prize_name") or dig(dbody, "prize") or "未知"
                    if not isinstance(prize, str):
                        # prize 可能是对象/数字；直接 += 会抛 TypeError，把一次已经中了的
                        # 抽奖变成"模块异常"，奖品名和下面这句提醒双双丢失
                        prize = str(prize)
                    # 奖池含实物周边（冰箱贴/胸针/杯子），中奖后要用户自己去填收件信息，
                    # 脚本代填不了也绝不该代填——但必须提醒，否则奖品会卡在未填地址状态。
                    if dig(dbody, "need_address") or dig(dbody, "require_address"):
                        prize += "（实物奖，需到成长中心填写收件信息）"
                    parts.append("开盲盒获得：%s" % prize)
                    successes += 1
                    # 一轮只开一次：这条写路径不可逆，剩下的机会留给下一轮更稳妥
                    if chances > 1:
                        parts.append("还剩 %s 次抽奖机会，下轮继续" % (chances - 1))
                else:
                    msg = dig(dbody, "msg") or ""
                    if _is_no_chance(msg):
                        # 次数为 0 是常态（次数来自连登兑换），不是故障：计入失败会让
                        # 轮询每轮强制落盘，还会把常态算进失败统计
                        parts.append("开盲盒：%s" % (msg or "无抽奖机会"))
                    else:
                        parts.append("开盲盒失败：%s" % (msg or "HTTP %s" % dcode))
                        failures += 1
                        hard_failures += _is_hard_failure(dcode)
        except Exception as e:
            parts.append("盲盒模块异常（%s: %s）" % (type(e).__name__, e))
            failures += 1
            hard_failures += 1

    # --- 6. Buddy 盲盒（能量攒够 cost_per_open 就开；能量没有其它消耗出口）---
    if _budget_left() <= 0:
        parts.append("时间预算耗尽，Buddy 盲盒跳过")
    else:
        try:
            qcode, qbody = get(base + "/buddy/quota", headers)
            if _check_auth(qcode):
                return 1, {"result": "NO_SESSION",
                           "report": "登录态已失效，请重新登录 WorkBuddy 桌面端"}
            if not _note_http(qcode, qbody, "查 Buddy 能量"):
                affordable = as_int(dig(qbody, "affordable"))
                max_open = as_int(dig(qbody, "max_open_count"), 1) or 1
                if affordable > 0:
                    count = min(affordable, max_open)
                    ocode, obody = post(base + "/buddy/open", headers,
                                        {"count": count, "client_token": _client_token()})
                    if _check_auth(ocode):
                        return 1, {"result": "NO_SESSION",
                                   "report": "登录态已失效，请重新登录 WorkBuddy 桌面端"}
                    if 200 <= ocode < 300:
                        name = dig(obody, "buddy") or dig(obody, "name") or dig(obody, "buddies")
                        if not isinstance(name, str):
                            name = "新 Buddy"
                        parts.append("开 Buddy 盲盒 ×%s（%s）" % (count, name))
                        successes += 1
                    else:
                        msg = dig(obody, "msg") or ""
                        parts.append("开 Buddy 盲盒失败：%s" % (msg or "HTTP %s" % ocode))
                        failures += 1
                        hard_failures += _is_hard_failure(ocode)
        except Exception as e:
            parts.append("Buddy 盲盒模块异常（%s: %s）" % (type(e).__name__, e))
            failures += 1
            hard_failures += 1

    # --- 7. 能量 & 连签状态（纯展示值，预算不够就直接不取，不计失败）---
    energy = None
    streak_days = None
    if _budget_left() > 0:
        try:
            ecode, ebody = get(base + "/energy", headers)
            if not _check_auth(ecode):
                energy = dig(ebody, "balance") if (200 <= ecode < 300) else None
        except Exception:
            pass

    # 连签天数：第 3 段已经取过 /streak，没补登过就直接复用，别在同一轮里打两次同一个接口。
    # 补登成功会改变天数（streak_stale），那时才有必要重新取。
    try:
        if streak_body is not None and not streak_stale:
            streak_obj = dig(streak_body, "streak") or {}
            streak_days = streak_obj.get("days") if isinstance(streak_obj, dict) else None
        elif _budget_left() > 0:
            scode2, sbody2 = get(base + "/streak", headers)
            if not _check_auth(scode2):
                streak_obj = dig(sbody2, "streak") or {}
                streak_days = streak_obj.get("days") if isinstance(streak_obj, dict) else None
    except Exception:
        pass

    tail = []
    if energy is not None:
        tail.append("能量 %s" % energy)
    if streak_days is not None:
        tail.append("连签 %s 天" % streak_days)
    if credits_gained:
        tail.append("本次 +共 %s 积分" % credits_gained)

    if parts:
        report = "；".join(parts)
    elif failures:
        report = "成长中心各步骤均失败"
    else:
        report = "成长中心无可领取项"
    if tail:
        report += "（%s）" % "，".join(tail)

    # 只有"确有需要关注的失败且一件都没成"才算整体失败。
    # 派 Buddy 已达每日上限这类 4xx 是每天的常态，不能让计划任务天天报红。
    result_code = 1 if (hard_failures and not successes) else 0
    # idle = 这一轮既没领到东西也没出错，纯空跑（Buddy 还在路上 / 今日名额已用完 /
    # 确实没有可领项）。轮询任务靠它决定要不要写日志。
    idle = (successes == 0 and failures == 0)
    return result_code, {"result": "GROWTH", "report": report, "credits_gained": credits_gained,
                         "energy": energy, "streak_days": streak_days, "idle": idle,
                         **({"failures": failures} if failures else {})}


def run_auto(headers, endpoint):
    """每日自动化主逻辑：查状态→未签才领→返回一行汇报。"""
    _sleep_jitter()
    scode, sbody = post(endpoint + "/v2/billing/meter/checkin-activity-status", headers, retry=True)

    if scode == CODE_BUDGET_OUT:
        return 1, {
            "result": "TIMEOUT",
            "report": "已达本次运行时间预算，签到跳过，下次自动重试",
        }
    if scode == CODE_NO_NETWORK:
        return 1, {
            "result": "NETWORK",
            "report": "网络不可达，签到跳过，下次自动重试（%s）" % (sbody.get("error") or ""),
            "error": sbody.get("error"),
        }
    if scode in (401, 403):
        return 1, {
            "result": "NO_SESSION",
            "report": "登录态已失效（HTTP %s），请重新登录 WorkBuddy 桌面端" % scode,
            "http": scode,
        }
    if not (200 <= scode < 300):
        # 401/403 归到 NO_SESSION：客户端没开或登录过期时，签到接口就是这么回的，
        # 把它当成笼统的 HTTP 异常会让人对着 "HTTP 401" 去猜是接口挂了还是登录过期。
        if scode in (401, 403):
            return 1, {
                "result": "NO_SESSION",
                "report": "登录态已失效（HTTP %s），请重新登录 WorkBuddy 桌面端" % scode,
                "http": scode,
            }
        return 1, {
            "result": "ERROR",
            "report": "签到接口返回异常（HTTP %s），请重新登录客户端或稍后重试" % scode,
            "http": scode,
            "status_body": sbody,
        }

    status = sbody if isinstance(sbody, dict) else {}
    active = dig(status, "active")
    activity_name = dig(status, "activity_name")

    if active is False:
        report = "签到活动未开启" + ("（%s）" % activity_name if activity_name else "")
        return 0, {"result": "INACTIVE", "report": report, "active": False}

    if dig(status, "today_checked_in") in (True, 1):
        return 0, _already_report(status)

    ccode, cbody = post(endpoint + "/v2/billing/meter/daily-checkin", headers, retry=True)

    if ccode in (CODE_NO_NETWORK, CODE_BUDGET_OUT):
        return 1, {
            "result": "NETWORK" if ccode == CODE_NO_NETWORK else "TIMEOUT",
            "report": "领取请求未能送达，下次自动重试（%s）" % (
                (cbody.get("error") or "") if isinstance(cbody, dict) else ""),
        }

    # 登录态判定要先于"已签"判定，避免失效时的报错体被误判为已领取
    if ccode in (401, 403):
        return 1, {
            "result": "NO_SESSION",
            "report": "登录态已失效（HTTP %s），请重新登录 WorkBuddy 桌面端" % ccode,
            "http": ccode,
        }

    if _is_already_checked_in(cbody):
        scode2, sbody2 = post(endpoint + "/v2/billing/meter/checkin-activity-status", headers, retry=True)
        fresh = sbody2 if (200 <= scode2 < 300 and isinstance(sbody2, dict)) else status
        return 0, _already_report(fresh, via="今日已签过（服务端判定已领取）")

    credit = dig(cbody, "credit")
    if credit is not None:
        scode2, sbody2 = post(endpoint + "/v2/billing/meter/checkin-activity-status", headers, retry=True)
        fresh = sbody2 if (200 <= scode2 < 300 and isinstance(sbody2, dict)) else status
        streak_days = dig(fresh, "streak_days") or dig(status, "streak_days")
        total_credits = dig(fresh, "total_credits")
        is_streak_day = dig(fresh, "is_streak_day")
        next_streak_day = dig(fresh, "next_streak_day")
        bonus = "，且为连签奖励日" if is_streak_day else ""
        cum = "，累计 %s 积分" % fmt_credit(total_credits) if total_credits is not None else ""
        # streak_days 缺失时不要把 None 打进文案
        streak = "（连续 %s 天%s）" % (streak_days, cum) if streak_days is not None else (
            "（%s）" % cum.lstrip("，") if cum else "")
        report = "成功领取 %s 积分%s%s" % (fmt_credit(credit), bonus, streak)
        return 0, {
            "result": "CLAIMED",
            "report": report,
            "credit": credit,
            "streak_days": streak_days,
            "total_credits": total_credits,
            "is_streak_day": is_streak_day,
            "next_streak_day": next_streak_day,
        }

    if isinstance(cbody, dict) and ("code" in cbody or "msg" in cbody):
        msg = cbody.get("msg") or ("code %s" % cbody.get("code"))
        return 1, {
            "result": "ERROR",
            "report": "领取失败：%s（HTTP %s）" % (msg, ccode),
            "http": ccode,
            "claim_body": cbody,
        }

    return 1, {
        "result": "UNKNOWN",
        "report": "未识别的领取返回，请检查接口：%s" % json.dumps(cbody, ensure_ascii=False)[:200],
        "http": ccode,
        "claim_body": cbody,
    }


def run_daily(headers, endpoint):
    """一轮完整的日常：查状态→未签才领→再跑成长中心。返回 (退出码, 输出, 是否空跑)。

    这是 auto / silent / silent-poll 三条路径的共用实现。轮询之所以要跑它而不是
    "只跑成长中心"，是因为签到原本一天只有 00:05 这一次机会：那一次撞上关机、
    睡眠、或刚开机网络还没就绪，当天就再无补救，连签直接断。让每一次轮询都带上
    签到，等于一天七次机会，且不会重复领取——接口幂等，已签会直接返回 ALREADY。

    第三个返回值是给轮询用的静默判据：这一轮没有任何值得一提的事（已签 + 成长
    中心无可领取项）。定时任务不关心它，照写日志；轮询靠它决定要不要落盘。
    """
    code, out = run_auto(headers, endpoint)
    # 网络本就不可达时不必再跑成长中心的一串请求（每个都要重试+等待），也免得
    # 汇报出"无可领取项"这种假的安心话
    if out.get("result") in ("NETWORK", "TIMEOUT"):
        out["growth"] = "网络不可达或时间预算耗尽，成长中心跳过"
        out["growth_result"] = out["result"]
        return code, out, False
    # 登录态已失效时同理：后面每个请求都只会再返回一次 401，白跑且刷屏
    if out.get("result") == "NO_SESSION":
        out["growth"] = "登录态已失效，成长中心跳过"
        out["growth_result"] = out["result"]
        return code, out, False
    # 签到后顺带跑成长中心；它出任何问题都不能吞掉签到已成功的事实
    try:
        gcode, gout = run_growth(headers, endpoint)
    except Exception as e:
        gcode, gout = 1, {"result": "ERROR",
                          "report": "成长中心异常（%s: %s）" % (type(e).__name__, e)}
    out["growth"] = gout.get("report")
    out["growth_result"] = gout.get("result")
    if gout.get("credits_gained"):
        out["report"] += "；" + gout["report"]
    # run_growth 只在"确有硬失败且一件都没成"时返回非 0（无可领取项、4xx 业务规则
    # 均返回 0），直接透传即可——之前按 result 枚举漏了 result=GROWTH 的整体失败
    if gcode != 0 and code == 0:
        code = gcode
    quiet = out.get("result") in ("ALREADY", "INACTIVE") and bool(gout.get("idle"))
    return code, out, quiet


def _run_trae(sub_action):
    """Trae CN 签到编排：找 storage.json → 解密 → 构建 header → 查/领。

    sub_action 取值：
      auto    → 未签才领，已签也报成功
      silent  → 同 auto，但走 silent 路径（结果落盘、空跑不刷屏）
      status  → 只查状态
      claim   → 直接领（不先查状态）
    """
    _start_budget(sub_action or "trae")
    if sub_action in ("auto", "silent", "silent-poll"):
        _sleep_jitter()

    storage_path, looked_in = find_trae_storage_file()
    if not storage_path:
        emit({"result": "NO_AUTH", "platform": "trae",
              "report": "未找到 Trae 桌面端 storage.json，请先登录 Trae 桌面端；"
                        "或设置环境变量 TRAE_AUTH_FILE 指向该文件",
              "looked_in": looked_in}, sub_action)
        return 2

    try:
        session = load_trae_session(storage_path)
        headers = build_trae_headers(session)
    except Exception as e:
        emit({"result": "ERROR", "platform": "trae",
              "report": "加载 Trae 会话失败（%s: %s）" % (type(e).__name__, e)}, sub_action)
        return 2

    if sub_action == "status":
        st = trae_status(headers)
        if "error" in st:
            emit({"step": "trae-status", "platform": "trae",
                  "result": "ERROR", "report": "签到状态查询失败", "body": st}, sub_action)
            return 1
        emit({"step": "trae-status", "platform": "trae",
              "checked_in": st["checked_in"], "credits": st["credits"],
              "enable": st.get("enable", True)}, sub_action)
        return 0

    if sub_action == "claim":
        cl = trae_claim(headers)
        if cl.get("ok"):
            # 强制领取路径没有先查 status，claim 成功响应又常常不带积分；
            # 拿不到数字时就不写"积分 0"，避免日志里出现误导性的 0。
            got = int(cl.get("credits") or 0)
            report = ("Trae CN 领取成功（积分 %d）" % got) if got \
                else "Trae CN 领取成功"
            emit({"result": "CLAIM", "platform": "trae", "report": report,
                  "credits": got or None, "code": cl.get("code"),
                  "message": cl.get("message")}, sub_action)
            return 0
        emit({"result": "ERROR", "platform": "trae",
              "report": "Trae CN 领取失败", "code": cl.get("code"),
              "message": cl.get("message"), "http": cl.get("http")}, sub_action)
        return 1

    # auto / silent / None：先查状态，未签才领
    st = trae_status(headers)
    if "error" in st:
        err = st["error"]
        if isinstance(err, int) and err == CODE_NO_NETWORK:
            result, report = "NETWORK", "网络不可达"
        else:
            result, report = "ERROR", "签到状态查询失败"
        emit({"result": result, "platform": "trae", "report": report, "body": st}, sub_action)
        return 1

    if st.get("checked_in"):
        emit({"result": "ALREADY", "platform": "trae",
              "report": "今日已签（Trae CN，积分 %d）" % st.get("credits", 0),
              "credits": st.get("credits", 0)}, sub_action)
        return 0

    if not st.get("enable", True):
        emit({"result": "INACTIVE", "platform": "trae",
              "report": "Trae CN 签到活动暂未开放", "credits": st.get("credits", 0)}, sub_action)
        return 0

    cl = trae_claim(headers)
    if cl.get("ok"):
        # claim 的成功响应常常只有 {"code":0,"message":"success"}，不回报积分；
        # 用先前 status 拿到的 credits 兜底，日志里才有可读的数字。
        got = cl.get("credits") or st.get("credits", 0)
        emit({"result": "CLAIM", "platform": "trae",
              "report": "Trae CN 领取成功（积分 %d）" % got,
              "credits": got}, sub_action)
        return 0
    emit({"result": "ERROR", "platform": "trae",
          "report": "Trae CN 领取失败", "code": cl.get("code"),
          "message": cl.get("message"), "http": cl.get("http")}, sub_action)
    return 1


def _run_qoder(sub_action):
    """Qoder 签到编排：找数据目录 → 解密凭据 → 查活动 → 领取全部可领积分项。

    Qoder 没有"每日签到"接口，等价动作是扫 campaigns 里所有 CLAIMABLE 的积分
    福利并逐个 claim；无活动记 INACTIVE、全部已领记 ALREADY。

    sub_action 取值：
      auto    → 领取全部可领项（没有可领也算成功）
      silent  → 同 auto，但走 silent 路径（结果落盘、空跑不刷屏）
      status  → 只报活动与可领状态，不发领取请求
      claim   → 同 auto（领取本就由服务端按 campaignId 幂等判定，无需强领分支）
    """
    _start_budget(sub_action or "qoder")
    if sub_action in ("auto", "silent"):
        _sleep_jitter()

    qdir, looked_in = find_qoder_dir()
    if not qdir:
        emit({"result": "NO_AUTH", "platform": "qoder",
              "report": "未找到 Qoder 桌面端数据目录（auth.v1.dat + Local State），"
                        "请先在本机登录 Qoder 桌面端；"
                        "或设置环境变量 QODER_AUTH_FILE 指向 auth.v1.dat",
              "looked_in": looked_in}, sub_action)
        return 2

    try:
        session = load_qoder_session(qdir)
    except Exception as e:
        emit({"result": "ERROR", "platform": "qoder",
              "report": "加载 Qoder 会话失败（%s: %s）" % (type(e).__name__, e)}, sub_action)
        return 2

    # token 临近过期先尝试刷新；刷新失败也带着旧 token 上路，
    # 让服务端用 401 说话，比我们本地猜时钟偏差可靠。
    exp = session.get("expires_at")
    if exp and exp - QODER_EXPIRY_MARGIN < time.time():
        qoder_refresh(session)

    res = qoder_campaigns(session)
    if not res.get("ok"):
        reason = res.get("reason")
        if reason == "network":
            emit({"result": "NETWORK", "platform": "qoder",
                  "report": "网络不可达", "body": res.get("body")}, sub_action)
        elif reason == "auth":
            emit({"result": "NO_SESSION", "platform": "qoder",
                  "report": "Qoder 登录态已失效（候选主机均返回 %s），"
                            "请在 Qoder 桌面端重新登录" % res.get("http"),
                  "http": res.get("http")}, sub_action)
        else:
            emit({"result": "ERROR", "platform": "qoder",
                  "report": "活动查询失败", "http": res.get("http"),
                  "body": res.get("body")}, sub_action)
        return 1

    campaigns = res.get("campaigns") or []
    picked = qoder_pick_claimable(campaigns)

    if sub_action == "status":
        total = sum(int(_q_benefit_of(c).get("amount") or 0) for c in picked)
        emit({"step": "qoder-status", "platform": "qoder",
              "campaigns": len(campaigns), "claimable_count": len(picked),
              "show_campaign": res.get("show_campaign"),
              "claimable": res.get("claimable"), "credits": total}, sub_action)
        return 0

    if not campaigns:
        emit({"result": "INACTIVE", "platform": "qoder",
              "report": "Qoder 当前无运营活动"}, sub_action)
        return 0

    if not picked:
        emit({"result": "ALREADY", "platform": "qoder",
              "report": "Qoder 活动福利均已领取（共 %d 个活动）" % len(campaigns)},
             sub_action)
        return 0

    got_total = 0
    got_count = 0
    fails = []
    for c in picked:
        amount = int(_q_benefit_of(c).get("amount") or 0)
        cl = qoder_claim(session, c.get("campaignId"), amount)
        if cl.get("ok"):
            got_count += 1
            got_total += amount
        else:
            fails.append({"campaignId": c.get("campaignId"),
                          "campaignKey": c.get("campaignKey"),
                          "http": cl.get("http"), "body": cl.get("body")})
        if fails or not _budget_left():
            # 一次领取失败大概率是登录态/活动状态问题，再连环 POST 只会制造废请求
            break

    if not fails:
        emit({"result": "CLAIM", "platform": "qoder",
              "report": "Qoder 领取成功（%d 项，积分 %d）" % (got_count, got_total),
              "credits": got_total}, sub_action)
        return 0
    if got_count:
        emit({"result": "PARTIAL", "platform": "qoder",
              "report": "Qoder 部分领取成功（%d 项 / 积分 %d，%d 项失败）" % (
                  got_count, got_total, len(fails)),
              "credits": got_total, "failed": fails}, sub_action)
        return 1
    if any(f.get("http") == CODE_NO_NETWORK for f in fails):
        emit({"result": "NETWORK", "platform": "qoder",
              "report": "网络不可达（领取失败）", "failed": fails}, sub_action)
        return 1
    emit({"result": "ERROR", "platform": "qoder",
          "report": "Qoder 领取失败", "failed": fails}, sub_action)
    return 1


def _run_both(silent=False):
    """全平台签到：WorkBuddy → Trae CN → Qoder，任一失败不影响其它平台。

    silent=True 时各子命令都用 silent 前缀（结果落盘、空跑不刷屏）。
    返回码取各平台最大值，任一失败即非零。
    """
    wb_action = "silent" if silent else "auto"
    tr_action = "silent" if silent else "auto"
    qd_action = "silent" if silent else "auto"

    # WorkBuddy 侧：走 _run 正常路径，action 不匹配 trae/qoder/both 前缀
    try:
        wb_code = _run(wb_action)
    except Exception as e:
        emit({"result": "ERROR", "platform": "workbuddy",
              "report": "WorkBuddy 侧异常（%s: %s）" % (type(e).__name__, e)}, wb_action)
        wb_code = 2

    # Trae CN 侧：即使 WorkBuddy 失败也要跑，因为它是独立平台
    try:
        tr_code = _run_trae(tr_action)
    except Exception as e:
        emit({"result": "ERROR", "platform": "trae",
              "report": "Trae 侧异常（%s: %s）" % (type(e).__name__, e)}, tr_action)
        tr_code = 2

    # Qoder 侧：同理，独立凭据、独立主机，与前两者成败互不相干
    try:
        qd_code = _run_qoder(qd_action)
    except Exception as e:
        emit({"result": "ERROR", "platform": "qoder",
              "report": "Qoder 侧异常（%s: %s）" % (type(e).__name__, e)}, qd_action)
        qd_code = 2

    return max(wb_code, tr_code, qd_code)


def main():
    """薄壳：只负责取命令 + 兜住一切异常，保证 silent 模式下结果必定落盘。"""
    # 支持多参数命令（如 "trae silent"、"qoder silent"、"both silent"）
    if len(sys.argv) > 1:
        action = sys.argv[1]
        # 特殊：把 "trae silent" / "qoder silent" / "both silent" 拼成一个整体命令名
        if action in ("trae", "qoder", "both") and len(sys.argv) > 2:
            action = action + " " + sys.argv[2]
    else:
        action = "auto"
    try:
        return _run(action)
    except Exception as e:
        # 无窗口运行下任何未捕获异常都会让当天的失败无痕消失，这里是最后一道防线
        emit({"result": "ERROR", "report": "脚本运行异常（%s: %s）" % (type(e).__name__, e)}, action)
        return 2


def _run(action):
    # --- 新命令：Trae CN / Qoder 与 全平台 both（不需要 WorkBuddy auth 文件） ---
    # 这些命令只走各自的存储路径，因此不能落入下面的 WorkBuddy auth 探测逻辑。
    if action == "trae":
        return _run_trae("auto")
    if action == "trae status":
        return _run_trae("status")
    if action == "trae claim":
        return _run_trae("claim")
    if action == "trae silent":
        return _run_trae("silent")
    if action == "qoder":
        return _run_qoder("auto")
    if action == "qoder status":
        return _run_qoder("status")
    if action == "qoder claim":
        return _run_qoder("claim")
    if action == "qoder silent":
        return _run_qoder("silent")
    if action in ("both", "both silent"):
        return _run_both(silent=("silent" in action))

    _start_budget(action)
    known = ("auto", "silent", "growth", "silent-poll", "silent-growth", "status", "claim", "all",
             "trae", "trae silent", "trae status", "trae claim",
             "qoder", "qoder silent", "qoder status", "qoder claim",
             "both", "both silent")
    if action not in known:
        emit({"result": "ERROR",
              "report": "未知命令：%s（可用：%s）" % (action, " / ".join(known))},
             action)
        return 2

    auth_file, looked_in = find_auth_file()
    env_override = os.environ.get("WORKBUDDY_AUTH_FILE")
    if not auth_file or not os.path.exists(auth_file):
        if env_override:
            report = ("WORKBUDDY_AUTH_FILE 指向的文件不存在：%s" % env_override)
        else:
            report = ("未找到 WorkBuddy 登录凭据。请先在本机登录 WorkBuddy 桌面端；"
                      "或设置环境变量 WORKBUDDY_AUTH_FILE 指向 workbuddy-desktop.info。")
        emit({"result": "NO_AUTH", "report": report, "looked_in": looked_in}, action)
        return 2

    try:
        session = load_session_retry(auth_file)
    except EncryptedAuthError as e:
        # 凭据被桌面端加密、且本次没解封成功。以前这里会一路走到 401 然后误报
        # "登录态已失效"，把排查方向带偏；现在如实说是解封失败，并带上原因。
        emit({"result": "ENCRYPTED_AUTH",
              "report": "登录凭据已被 WorkBuddy 桌面端加密，本次未能解封：%s" % e,
              "credential_file": auth_file}, action)
        return 2
    except json.JSONDecodeError as e:
        # JSONDecodeError 是 ValueError 的子类，必须先于下面的分支捕获，否则会被误归类
        emit({"result": "ERROR",
              "report": "登录凭据文件不是合法 JSON（%s），请重新登录 WorkBuddy 桌面端" % e},
             action)
        return 2
    except ValueError as e:
        # 文件编码损坏（UnicodeDecodeError 也是 ValueError 子类）等情形
        emit({"result": "ERROR",
              "report": "登录凭据文件内容损坏（%s: %s），请重新登录 WorkBuddy 桌面端" % (type(e).__name__, e)},
             action)
        return 2
    except Exception as e:
        # 无读取权限等其它 IO 问题
        emit({"result": "ERROR",
              "report": "读取登录凭据失败（%s: %s），请重新登录 WorkBuddy 桌面端" % (type(e).__name__, e)},
             action)
        return 2

    try:
        headers = build_headers(session)
    except EncryptedAuthError as e:
        emit({"result": "ENCRYPTED_AUTH",
              "report": "登录凭据已被 WorkBuddy 桌面端加密，本次未能解封：%s" % e,
              "credential_file": auth_file}, action)
        return 2
    except ValueError as e:
        emit({"result": "NO_SESSION", "report": str(e)}, action)
        return 1

    endpoint = ((session.get("auth") or {}).get("endpoint") or DEFAULT_ENDPOINT).rstrip("/")

    if action in ("auto", "silent"):
        code, out, _ = run_daily(headers, endpoint)
        emit(out, action)
        return code

    if action in POLL_ACTIONS:
        # 轮询 = 补签 + 成长中心，结果写日志文件（emit 按 silent 前缀判定落盘）。
        # 早期版本这里只跑成长中心、刻意不碰签到接口，理由是"签到一天一次就够"；
        # 但那样一来 00:05 那次一旦失败（关机/睡眠/刚开机网络没就绪），当天就再无
        # 第二次机会。现在每次轮询都先查一次签到状态，未签才补——已签的代价只是
        # 一个查询请求，换来的是一天七次机会。
        # 空跑默认不落盘——一天要跑好几轮，全写进去只会把真正有价值的记录
        # 淹没在一串"Buddy 旅行中"里。要看完整过程就设 WORKBUDDY_GROWTH_LOG_EMPTY=1。
        code, out, quiet = run_daily(headers, endpoint)
        out["trigger"] = "poll"
        if (not quiet) or _env_flag("WORKBUDDY_GROWTH_LOG_EMPTY"):
            emit(out, action)
        return code

    if action == "growth":
        code, out = run_growth(headers, endpoint)
        emit(out, action)
        return code

    # 以下为交互式调试命令，输出原始返回。走 emit 而非 print，这样非法配置的
    # config_warning 同样能带出来（这些命令不会是 silent，仍然打到 stdout）
    if action in ("status", "all"):
        scode, sbody = post(endpoint + "/v2/billing/meter/checkin-activity-status", headers, retry=True)
        emit({"step": "status", "http": scode, "body": sbody}, action)

    if action in ("claim", "all"):
        ccode, cbody = post(endpoint + "/v2/billing/meter/daily-checkin", headers, retry=True)
        emit({"step": "claim", "http": ccode, "body": cbody}, action)

    return 0

if __name__ == "__main__":
    sys.exit(main())
