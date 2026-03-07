#!/usr/bin/env python3
"""
冒烟测试：验证人声分离服务是否可正常交互。
无需额外依赖（仅用标准库）。需先启动服务：uvicorn main:app --host 0.0.0.0 --port 8001

用法:
  python smoke_test.py [BASE_URL]
  默认 BASE_URL=http://127.0.0.1:8001
"""
import os
import sys
import tempfile
import urllib.error
import urllib.request
import wave


def make_minimal_wav(path: str, duration_sec: float = 0.5) -> None:
    """生成极短的合法 WAV（静音），用于快速验证接口。"""
    rate = 16000
    nchannels = 1
    sampwidth = 2  # 16-bit
    nframes = int(rate * duration_sec)
    with wave.open(path, "wb") as w:
        w.setnchannels(nchannels)
        w.setsampwidth(sampwidth)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * nframes)


def test_healthz(base_url: str) -> bool:
    """GET /healthz，期望 200 且 body 含 status ok。"""
    url = f"{base_url.rstrip('/')}/healthz"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as r:
            if r.status != 200:
                print(f"  healthz 状态码: {r.status}，期望 200")
                return False
            body = r.read().decode()
            if "ok" not in body:
                print(f"  healthz 返回: {body}")
                return False
            print("  healthz: OK")
            return True
    except urllib.error.URLError as e:
        print(f"  healthz 请求失败: {e}")
        return False


def test_separate(base_url: str, wav_path: str) -> bool:
    """POST 小 WAV 到 /api/v1/separate，期望 200 且返回 zip。"""
    url = f"{base_url.rstrip('/')}/api/v1/separate"
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    with open(wav_path, "rb") as f:
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="audio"; filename="smoke.wav"\r\n'
            "Content-Type: audio/wav\r\n\r\n"
        ).encode() + f.read() + (
            f"\r\n--{boundary}\r\n"
            'Content-Disposition: form-data; name="model"\r\n\r\nhtdemucs\r\n'
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="stems"\r\n\r\n2\r\n'
            f"--{boundary}--\r\n"
        ).encode()

    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            if r.status != 200:
                print(f"  separate 状态码: {r.status}，期望 200")
                return False
            ct = r.headers.get("Content-Type", "")
            content = r.read()
            if "zip" not in ct and not content.startswith(b"PK"):
                print(f"  separate 返回类型: {ct}，期望 zip；前 20 字节: {content[:20]}")
                return False
            print(f"  separate: OK（返回 zip，{len(content)} 字节）")
            return True
    except urllib.error.HTTPError as e:
        print(f"  separate 请求失败: {e.code} {e.reason}")
        try:
            print(f"  响应: {e.read().decode()[:500]}")
        except Exception:
            pass
        return False
    except urllib.error.URLError as e:
        print(f"  separate 请求失败: {e}")
        return False


def main() -> int:
    base_url = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001").strip()
    print(f"Base URL: {base_url}")
    print("1. 健康检查 /healthz")
    if not test_healthz(base_url):
        return 1
    print("2. 人声分离 /api/v1/separate（小 WAV）")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav_path = f.name
    try:
        make_minimal_wav(wav_path)
        if not test_separate(base_url, wav_path):
            return 1
    finally:
        os.unlink(wav_path)
    print("全部通过，服务可正常交互。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
