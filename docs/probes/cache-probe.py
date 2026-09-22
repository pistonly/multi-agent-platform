#!/usr/bin/env python3
"""探测当前 ANTHROPIC_BASE_URL 指向的网关是否支持 prompt caching。

两轮相同请求：第 1 轮应产生 cache_creation，第 2 轮应产生 cache_read。
官方 Anthropic API 会返回 cache_creation_input_tokens / cache_read_input_tokens
两个字段；中转网关若做协议转换，通常两个字段都消失。

用法：
    python docs/probes/cache-probe.py                 # 读 .map/.claude-env
    python docs/probes/cache-probe.py --base-url URL --token KEY --model NAME

成本：约 2 x 2600 input token，可忽略。只读探测，不写任何文件。
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

# Sonnet prompt caching 最小门槛 1024 token，system 需足够长
FILLER = (
    "Claude Code is an agentic coding tool that runs in the terminal. "
    "It uses a large context window and relies on prompt caching for efficiency. "
)


def load_env(root: Path) -> tuple[str, str, str]:
    env_file = root / ".map" / ".claude-env"
    if not env_file.exists():
        raise SystemExit(f"缺少 {env_file}")
    text = env_file.read_text()

    def pick(key: str) -> str:
        m = re.search(rf"{key}\s*=\s*[\"']?([^\s\"']+)", text)
        if not m:
            raise SystemExit(f"{env_file} 中找不到 {key}")
        return m.group(1)

    return pick("ANTHROPIC_BASE_URL"), pick("ANTHROPIC_AUTH_TOKEN"), pick("ANTHROPIC_MODEL")


def call(base: str, token: str, model: str, system_text: str) -> dict:
    body = {
        "model": model,
        "max_tokens": 16,
        "system": [{"type": "text", "text": system_text,
                    "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
    }
    req = urllib.request.Request(
        base.rstrip("/") + "/v1/messages",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "x-api-key": token,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        raise SystemExit(f"HTTP {exc.code}: {detail}") from exc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".", help="仓库根（用于定位 .map/.claude-env）")
    ap.add_argument("--base-url")
    ap.add_argument("--token")
    ap.add_argument("--model")
    args = ap.parse_args()

    if args.base_url and args.token and args.model:
        base, token, model = args.base_url, args.token, args.model
    else:
        base, token, model = load_env(Path(args.root))

    system_text = FILLER * 90
    print(f"网关   : {base}")
    print(f"模型   : {model}")
    print(f"system : {len(system_text)} 字符（>1024 token 缓存门槛）")
    print()

    first = call(base, token, model, system_text)
    u1 = first.get("usage", {})
    print(f"第 1 轮 usage: {json.dumps(u1)}")
    time.sleep(3)
    second = call(base, token, model, system_text)
    u2 = second.get("usage", {})
    print(f"第 2 轮 usage: {json.dumps(u2)}")
    print()

    has_fields = any(
        k in u for u in (u1, u2)
        for k in ("cache_creation_input_tokens", "cache_read_input_tokens")
    )
    created = u1.get("cache_creation_input_tokens", 0) or 0
    read = u2.get("cache_read_input_tokens", 0) or 0

    print("=== 判定 ===")
    if not has_fields:
        print("△ usage 中不存在任何 cache 字段。")
        print("  这不等于「没有缓存」——取决于网关背后的上游是什么：")
        print()
        print("  · 上游是 vLLM / SGLang 等自部署推理服务：服务端 Automatic Prefix")
        print("    Caching（RadixAttention，vLLM v1 默认开启）是**自动生效**的，")
        print("    不需要客户端传 cache_control，也不回报 usage 字段。")
        print("    → 缓存很可能已经在省算力，本脚本测不出来。请直接查上游：")
        print("      curl http://<vllm>/metrics | grep -i cache        # 命中率")
        print("      curl http://<vllm>/v1/chat/completions ...        # 看 usage.prompt_tokens_details.cached_tokens")
        print()
        print("  · 上游是订阅账号（OAuth）或做协议转换的中转：cache_control 在转换中")
        print("    丢失，且订阅后端不暴露缓存计费字段 → 确实无缓存收益。")
    elif created == 0 and read == 0:
        print("△ 有 cache 字段但两轮均为 0 → 网关透传了字段，但上游未启用缓存。")
    elif read > 0:
        print(f"✓ 缓存命中：第 2 轮 cache_read = {read:,} token（单价约 0.1x）。")
    else:
        print(f"✓ 缓存写入：第 1 轮 cache_creation = {created:,}，"
              "但第 2 轮未命中——检查是否 5 分钟内过期或前缀不一致。")


if __name__ == "__main__":
    main()
