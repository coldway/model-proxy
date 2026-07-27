#!/usr/bin/env python3
# Created by AI on 2026/05/27
# Copyright © 2026

"""Cursor Agent CLI Plan 模式快速验证脚本"""

import json
import sys
from openai import OpenAI

def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)

def test_plan_mode():
    """测试 plan 模式"""
    print_section("1. Plan 模式测试（只读分析）")
    
    client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="unused")
    
    try:
        response = client.chat.completions.create(
            model="cursor-agent",
            messages=[
                {"role": "user", "content": "分析这个 model-proxy 项目的架构设计"}
            ],
            extra_body={"mode": "plan"}
        )
        
        print("✅ Plan 模式调用成功")
        print(f"\n响应内容:\n{response.choices[0].message.content}\n")
        
        # 打印路由信息
        if hasattr(response, 'proxy_info') or 'proxy_info' in response.model_extra:
            proxy_info = response.model_extra.get('proxy_info', {})
            print(f"Provider: {proxy_info.get('provider', 'N/A')}")
            print(f"Latency: {proxy_info.get('latency_ms', 0):.1f}ms")
        
        return True
    except Exception as e:
        print(f"❌ Plan 模式调用失败: {e}")
        return False

def test_ask_mode():
    """测试 ask 模式"""
    print_section("2. Ask 模式测试（只读问答）")
    
    client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="unused")
    
    try:
        response = client.chat.completions.create(
            model="cursor-agent",
            messages=[
                {"role": "user", "content": "这个项目主要解决什么问题？"}
            ],
            extra_body={"mode": "ask"}
        )
        
        print("✅ Ask 模式调用成功")
        print(f"\n响应内容:\n{response.choices[0].message.content}\n")
        return True
    except Exception as e:
        print(f"❌ Ask 模式调用失败: {e}")
        return False

def test_agent_mode():
    """测试 agent 模式（默认）"""
    print_section("3. Agent 模式测试（默认，全权限）")
    
    client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="unused")
    
    try:
        response = client.chat.completions.create(
            model="cursor-agent",
            messages=[
                {"role": "user", "content": "列出项目根目录的文件"}
            ]
            # 不指定 mode，默认为 agent
        )
        
        print("✅ Agent 模式调用成功")
        print(f"\n响应内容:\n{response.choices[0].message.content}\n")
        return True
    except Exception as e:
        print(f"❌ Agent 模式调用失败: {e}")
        return False

def test_stream_with_plan():
    """测试流式 + plan 模式"""
    print_section("4. 流式输出 + Plan 模式测试")
    
    client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="unused")
    
    try:
        stream = client.chat.completions.create(
            model="cursor-agent",
            messages=[
                {"role": "user", "content": "总结这个项目的核心功能"}
            ],
            stream=True,
            extra_body={"mode": "plan"}
        )
        
        print("✅ 流式 Plan 模式连接成功")
        print("\n流式响应内容:")
        
        for chunk in stream:
            if chunk.choices[0].delta.content:
                print(chunk.choices[0].delta.content, end="", flush=True)
        
        print("\n")
        return True
    except Exception as e:
        print(f"❌ 流式 Plan 模式失败: {e}")
        return False

def test_raw_api():
    """测试原始 HTTP API"""
    print_section("5. 原始 HTTP API 测试")
    
    import requests
    
    payload = {
        "model": "cursor-agent",
        "messages": [
            {"role": "user", "content": "简要说明这个项目的价值"}
        ],
        "mode": "plan"
    }
    
    try:
        response = requests.post(
            "http://127.0.0.1:8000/v1/chat/completions",
            json=payload,
            timeout=60
        )
        response.raise_for_status()
        
        data = response.json()
        print("✅ HTTP API 调用成功")
        print(f"\n响应内容:\n{data['choices'][0]['message']['content']}\n")
        
        if 'proxy_info' in data:
            print(f"Proxy Info: {json.dumps(data['proxy_info'], indent=2)}")
        
        return True
    except Exception as e:
        print(f"❌ HTTP API 调用失败: {e}")
        return False

def main():
    print("""
╔══════════════════════════════════════════════════════════════╗
║  Cursor Agent CLI Plan 模式验证脚本                           ║
║                                                                ║
║  将依次测试：                                                  ║
║  1. Plan 模式（只读分析）                                      ║
║  2. Ask 模式（只读问答）                                       ║
║  3. Agent 模式（默认，全权限）                                 ║
║  4. 流式输出 + Plan 模式                                       ║
║  5. 原始 HTTP API                                             ║
╚══════════════════════════════════════════════════════════════╝
    """)
    
    print("⚠️  请确保 model-proxy 服务已启动在 http://127.0.0.1:8000")
    print("⚠️  请确保 Cursor CLI 已安装并登录")
    
    input("\n按 Enter 键开始测试...")
    
    results = []
    
    results.append(("Plan 模式", test_plan_mode()))
    results.append(("Ask 模式", test_ask_mode()))
    results.append(("Agent 模式", test_agent_mode()))
    results.append(("流式 Plan 模式", test_stream_with_plan()))
    results.append(("HTTP API", test_raw_api()))
    
    print_section("测试结果汇总")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"{name:20s} : {status}")
    
    print(f"\n总计: {passed}/{total} 通过")
    
    if passed == total:
        print("\n🎉 所有测试通过！Cursor Agent CLI Plan 模式已成功集成。")
        return 0
    else:
        print(f"\n⚠️  {total - passed} 个测试失败，请检查错误信息。")
        return 1

if __name__ == "__main__":
    sys.exit(main())
