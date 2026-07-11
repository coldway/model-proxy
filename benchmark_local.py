# Created by yuanrui on 2026/07/03
# Copyright © 2026
#
# 本地 LLM 推理引擎性能对比测试
# 对比: Ollama vs mlx-lm vs Rapid-MLX
# 测试指标: TTFT（首token延迟）、吞吐量（tok/s）、总延迟

import json
import time
import httpx
import sys
from dataclasses import dataclass, field

PROMPT_SHORT = "用一句话解释什么是量子计算"
PROMPT_MEDIUM = """你是一位资深的后端工程师。请分析以下Go代码可能存在的性能问题，并给出优化建议：

```go
func GetUsersByIDs(ctx context.Context, ids []string) ([]*User, error) {
    var users []*User
    for _, id := range ids {
        user, err := db.QueryRow(ctx, "SELECT * FROM users WHERE id = ?", id)
        if err != nil {
            return nil, err
        }
        users = append(users, user)
    }
    return users, nil
}
```

请从数据库查询优化、并发处理、错误处理三个方面分析。"""

PROMPT_LONG = PROMPT_MEDIUM + "\n\n另外，请给出完整的重构后代码，包含连接池配置、批量查询、以及 context 超时处理。要求代码可以直接运行。"


@dataclass
class BenchmarkResult:
    engine: str
    model: str
    prompt_type: str
    ttft_ms: float = 0
    total_ms: float = 0
    total_tokens: int = 0
    tps: float = 0
    error: str = ""


def benchmark_openai_compatible(
    base_url: str,
    model: str,
    prompt: str,
    engine_name: str,
    prompt_type: str,
    max_tokens: int = 512,
) -> BenchmarkResult:
    """通用 OpenAI 兼容 API 测试（流式）"""
    result = BenchmarkResult(engine=engine_name, model=model, prompt_type=prompt_type)

    try:
        start = time.perf_counter()
        first_token_time = None
        token_count = 0

        with httpx.Client(timeout=120) as client:
            with client.stream(
                "POST",
                f"{base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "stream": True,
                    "temperature": 0.7,
                },
            ) as resp:
                for line in resp.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            if first_token_time is None:
                                first_token_time = time.perf_counter()
                            token_count += 1
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

        end = time.perf_counter()
        result.total_ms = (end - start) * 1000
        if first_token_time:
            result.ttft_ms = (first_token_time - start) * 1000
        result.total_tokens = token_count
        if result.total_ms > 0:
            result.tps = token_count / ((end - start))

    except Exception as e:
        result.error = str(e)

    return result


def benchmark_ollama_generate(
    model: str, prompt: str, prompt_type: str, max_tokens: int = 512
) -> BenchmarkResult:
    """Ollama /api/generate 测试（流式，更精确的 token 统计）"""
    result = BenchmarkResult(engine="Ollama", model=model, prompt_type=prompt_type)

    try:
        start = time.perf_counter()
        first_token_time = None
        token_count = 0

        with httpx.Client(timeout=120) as client:
            with client.stream(
                "POST",
                "http://localhost:11434/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": True,
                    "options": {"num_predict": max_tokens, "temperature": 0.7},
                },
            ) as resp:
                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                        if chunk.get("response"):
                            if first_token_time is None:
                                first_token_time = time.perf_counter()
                            token_count += 1
                        if chunk.get("done"):
                            eval_count = chunk.get("eval_count", token_count)
                            eval_duration = chunk.get("eval_duration", 0)
                            if eval_count and eval_duration:
                                result.tps = eval_count / (eval_duration / 1e9)
                                result.total_tokens = eval_count
                    except json.JSONDecodeError:
                        continue

        end = time.perf_counter()
        result.total_ms = (end - start) * 1000
        if first_token_time:
            result.ttft_ms = (first_token_time - start) * 1000
        if result.total_tokens == 0:
            result.total_tokens = token_count
        if result.tps == 0 and result.total_ms > 0:
            result.tps = token_count / ((end - start))

    except Exception as e:
        result.error = str(e)

    return result


def print_results(results: list[BenchmarkResult]):
    """格式化输出结果"""
    print("\n" + "=" * 90)
    print(f"{'引擎':<15} {'模型':<25} {'Prompt':<8} {'TTFT(ms)':<10} {'TPS':<10} {'Tokens':<8} {'总延迟(ms)':<12}")
    print("-" * 90)
    for r in results:
        if r.error:
            print(f"{r.engine:<15} {r.model:<25} {r.prompt_type:<8} {'ERROR: ' + r.error[:40]}")
        else:
            print(f"{r.engine:<15} {r.model:<25} {r.prompt_type:<8} {r.ttft_ms:<10.0f} {r.tps:<10.1f} {r.total_tokens:<8} {r.total_ms:<12.0f}")
    print("=" * 90)


def main():
    results = []

    test_model_ollama = "qwen2.5:7b"
    test_model_mlx = "mlx-community/Qwen2.5-7B-Instruct-4bit"
    test_model_rapid = "mlx-community/Qwen3-8B-4bit"

    prompts = [
        ("short", PROMPT_SHORT, 128),
        ("medium", PROMPT_MEDIUM, 512),
    ]

    engines = []

    # 检测可用引擎
    try:
        r = httpx.get("http://localhost:11434/api/tags", timeout=3)
        if r.status_code == 200:
            engines.append("ollama")
            print("✓ Ollama 可用")
    except Exception:
        print("✗ Ollama 不可用")

    try:
        r = httpx.get("http://localhost:8888/v1/models", timeout=3)
        if r.status_code == 200:
            engines.append("rapid-mlx")
            print("✓ Rapid-MLX 可用 (port 8888)")
    except Exception:
        print("✗ Rapid-MLX 不可用（需要先启动: rapid-mlx serve qwen3-8b-4bit --port 8888）")

    try:
        r = httpx.get("http://localhost:8081/v1/models", timeout=3)
        if r.status_code == 200:
            engines.append("mlx-lm")
            print("✓ mlx-lm 可用 (port 8081)")
    except Exception:
        print("✗ mlx-lm 不可用（需要先启动: mlx_lm.server --port 8081）")

    if not engines:
        print("\n没有可用的推理引擎，退出")
        sys.exit(1)

    print(f"\n可用引擎: {', '.join(engines)}")
    print(f"测试轮次: {len(prompts)} 个 prompt × {len(engines)} 个引擎")
    print("每个测试运行 2 次，取第 2 次结果（预热后）\n")

    for prompt_type, prompt, max_tokens in prompts:
        print(f"--- 测试 {prompt_type} prompt ---")

        if "ollama" in engines:
            # 预热
            print(f"  Ollama 预热中...")
            benchmark_ollama_generate(test_model_ollama, "hi", "warmup", 5)
            # 正式测试
            print(f"  Ollama 测试中...")
            r = benchmark_ollama_generate(test_model_ollama, prompt, prompt_type, max_tokens)
            results.append(r)

        if "rapid-mlx" in engines:
            print(f"  Rapid-MLX 预热中...")
            benchmark_openai_compatible("http://localhost:8888/v1", test_model_rapid, "hi", "Rapid-MLX", "warmup", 5)
            print(f"  Rapid-MLX 测试中...")
            r = benchmark_openai_compatible("http://localhost:8888/v1", test_model_rapid, prompt, "Rapid-MLX", prompt_type, max_tokens)
            results.append(r)

        if "mlx-lm" in engines:
            print(f"  mlx-lm 预热中...")
            benchmark_openai_compatible("http://localhost:8081/v1", test_model_mlx, "hi", "mlx-lm", "warmup", 5)
            print(f"  mlx-lm 测试中...")
            r = benchmark_openai_compatible("http://localhost:8081/v1", test_model_mlx, prompt, "mlx-lm", prompt_type, max_tokens)
            results.append(r)

    print_results(results)

    if "rapid-mlx" in engines:
        print("\n--- Prompt Cache 测试（Rapid-MLX 独有） ---")
        print("  第 1 次请求...")
        r1 = benchmark_openai_compatible("http://localhost:8888/v1", test_model_rapid, PROMPT_MEDIUM, "Rapid-MLX", "cache-cold", 128)
        print("  第 2 次相同请求（应命中缓存）...")
        r2 = benchmark_openai_compatible("http://localhost:8888/v1", test_model_rapid, PROMPT_MEDIUM, "Rapid-MLX", "cache-hot", 128)
        print(f"\n  冷启动 TTFT: {r1.ttft_ms:.0f}ms")
        print(f"  缓存命中 TTFT: {r2.ttft_ms:.0f}ms")
        if r1.ttft_ms > 0:
            print(f"  加速比: {r1.ttft_ms / max(r2.ttft_ms, 1):.1f}x")


if __name__ == "__main__":
    main()
