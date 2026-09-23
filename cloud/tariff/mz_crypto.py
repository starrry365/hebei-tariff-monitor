# -*- coding: utf-8 -*-
"""nrapigate 网关加解密（从 H5 前端 chunk-common.js 模块 0xaa31 还原）。

算法形态（密钥本体不写在本文件里）：
  key = 32 字节 ASCII                -> AES-256
  iv  = 16 字节 ASCII
  H4  = AES-CBC/Pkcs7 encrypt -> base64（urlSafe 时做 -_ 替换、去 padding）
  lT  = base64(url-safe 兼容) -> AES-CBC/Pkcs7 decrypt -> utf8

密钥取值顺序：
  1) 环境变量 ``NRAPIGATE_KEY`` / ``NRAPIGATE_IV``   —— CI 上由 Actions Secret 注入
  2) 同目录下 ``.nrapigate_key`` 文件（本地开发用，已被 .gitignore 忽略）
     文件格式：每行 ``KEY=VALUE``，支持 ``#`` 注释

⚠️ 不要把密钥写回源码 —— 本仓库公开，历史上曾因此泄露一次。
"""
import base64
import os

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad, unpad
except ImportError:  # pragma: no cover
    from Cryptodome.Cipher import AES
    from Cryptodome.Util.Padding import pad, unpad

_HERE = os.path.dirname(os.path.abspath(__file__))
_KEY_PATH = os.path.join(_HERE, ".nrapigate_key")


def _load_credentials():
    """返回 (key: bytes, iv: bytes)；取不到就抛错，绝不静默回退到硬编码值。"""
    env_k, env_v = os.environ.get("NRAPIGATE_KEY"), os.environ.get("NRAPIGATE_IV")
    if env_k and env_v:
        return env_k.encode("utf-8"), env_v.encode("utf-8")

    if os.path.exists(_KEY_PATH):
        kv = {}
        with open(_KEY_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                kv[k.strip()] = v.strip()
        if kv.get("KEY") and kv.get("IV"):
            return kv["KEY"].encode("utf-8"), kv["IV"].encode("utf-8")

    raise RuntimeError(
        "未找到 nrapigate 网关密钥。请二选一：\n"
        "  · 设置环境变量 NRAPIGATE_KEY / NRAPIGATE_IV\n"
        "  · 在本文件同目录放置 .nrapigate_key（内容：KEY=... 与 IV=...）")


KEY, IV = _load_credentials()

assert len(KEY) == 32, "AES-256 需要 32 字节密钥，当前 %d" % len(KEY)
assert len(IV) == 16, "CBC 需要 16 字节 IV，当前 %d" % len(IV)


def _b64_to_std(s):
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s)


def _to_urlsafe(b64):
    return b64.replace("+", "-").replace("/", "_").rstrip("=")


def decrypt(cipher_text, urlsafe_aware=True):
    """密文(base64) -> 明文字符串"""
    raw = _b64_to_std(cipher_text) if urlsafe_aware else base64.b64decode(cipher_text)
    pt = AES.new(KEY, AES.MODE_CBC, IV).decrypt(raw)
    return unpad(pt, 16).decode("utf-8")


def encrypt(plain, urlsafe=False):
    """明文字符串 -> 密文(base64)"""
    ct = AES.new(KEY, AES.MODE_CBC, IV).encrypt(pad(plain.encode("utf-8"), 16))
    b64 = base64.b64encode(ct).decode()
    return _to_urlsafe(b64) if urlsafe else b64


if __name__ == "__main__":
    # 来自 Fiddler 抓包的真实密文样本（密文可公开，密钥不可）
    samples = [
        ("userAuth 请求体",
         "96mU234Z5poPE_oEny1xJw6u9ZP2vWdBQhyRM25G9fyKYKhVeBxXys4undvZP_8xt3B5reVy7HmZwZ_y4qY9Ml"
         "GBzL04rDU45pL3Z-TSxXyrEosacuWmbrfpqq4MmiOO3zL7h6F-1RIXU3Zm5KfqjW5jrnkRpmiaGFsmNLZKsoR"
         "E_VRqOuoXI8sNAK5Kls60q7AKEwlwP_pc0FtpHpt2eSkeMa1eYyAk6BkhjE4IIVg="),
        ("getProductEquityList 请求体",
         "rMRezKixm1DdJikwGKx-vRfTzTc0e9B4zfQFa7OyS_1lY_ofFAN0AoKEZsaabBlP"),
        ("getProductEquityList 响应体",
         "JddRWDcJFqmaCqlqHuounSIqXFQq23U4Li9Kj55nR3KhUTswZeBYFJV72JtpdloRmJHLzXCX6McW5izcW5RDaFKYJ"
         "lL1PFemX99e0uu3Q-FbL9fi_kXea0reDXcZ1S0inMfPOyvwdKHGhzxfvKSPvCUfOwGLG5TwaBBVyjjDWLdFy6iJr"
         "iy47nCXzAgIztcKErzIiAUBOLxPVlvzJMLeyt9xZja-a0sFQapmlGPeSKeGM6ZF-35tmOKC6QS6WIausIqpcaV5Z8"
         "tlvYztwD4NrL_Eoul8K-6FKZnROgsYr9caERdNrHXAg9BYWQ5pj722DDZZ3aDA57oD2zxw46x83weXAy2DzNlTCg"
         "37BX6Xn7ZHq4RkAa9uO6YK2SxFhYQLGSXhD9MwCbKWCjVzSfGuKS69_Qp2TcmPe1M6CLgqddYlj07aqE1dok1UExw"
         "kxZaME6T7Uqas8bmbaBuWY5pVhFQvPZARMpnj_nPPT9BZgnkbC1Q5S0n5VM1gsnWucZd9n1HagQLnZeEBj0B9loaE"
         "YOp1jRRn1DpEKamuM__9WDGUslI9_dK_bRvg4viRMFgdju6QfjfuY22GoDFIzocVoWy9czi5VJhn3R6L_FZUYpfa"
         "gRj8VAtdLEKwwaGWE3Y7k2wZmR6s_iPfpAEhAuYBHvR-xd_pb8HX0sogMXAffd2AnxTr_lPmU7wsX6Qf4kO6WkBZ5"
         "t7d0WYZQZxyNNkwpuQt-y6yGdcsGIrCnwX36WSrskGYPxyfSvY1ChhF5WHz1n67QBkWmviCH1euJ-fg2Rmtvcq2YD"
         "2H1vj49n58j3IsZMNVQZD8fDYb9_GdQGCXIMdSuT4tJBm1ZZX9WZ6fGa6ux6sjnVGsSBOteJe9Rkx6dmD6khv2MdN"
         "JJ2js1ItjhYmQ-iuLzxS7kQmezL8OooTqW7YBmpeB9AxXJIrNOEDyJRitKkqeWdFOQrPKcj4hWvmm9MLpSUfxEQC"
         "MLwpEx2YEJSmJZS13acXOSuk3VIWChKPlrdCvEp1Gh-ZurwMyeOWh4O7Tg0EjPUU10Ty4xgngmauWSpGF1f_EE3A"
         "TUA8bjwr3vcLq3G83wwQqBCmAR53PCVurYwYDKPu7ABA9KA2VBKbod_p6ix_nrO_s1Jo="),
    ]
    for name, ct in samples:
        print("=" * 74)
        print("###", name)
        try:
            print(decrypt(ct)[:1200])
        except Exception as e:
            print("   FAIL:", type(e).__name__, e)
    print("=" * 74)
    print("自检 encrypt/decrypt 往返:", decrypt(encrypt('{"reqBody":{}}')))
