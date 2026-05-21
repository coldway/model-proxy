# Created by model-proxy on 2026/05/21
# Copyright © 2026

"""结构化输出测试 API"""

from __future__ import annotations

import json
import time
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.api.routes_pkg.deps import _deps
from src.api.routes_pkg.chat_completions import _extract_json_from_response
from src.models.schemas import ChatCompletionRequest, ChatMessage

router = APIRouter()


class StructuredTestRequest(BaseModel):
    model: str | None = None
    schema_level: str = "basic"


_STRUCTURED_TEST_SCHEMAS = {
    "basic": {
        "prompt": "列出3种编程语言",
        "schema": {
            "type": "object",
            "properties": {
                "languages": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"name": {"type": "string"}, "year": {"type": "integer"}}, "required": ["name", "year"]},
                },
            },
            "required": ["languages"],
        },
    },
    "nested": {
        "prompt": "描述一个Web应用的技术栈",
        "schema": {
            "type": "object",
            "properties": {
                "app_name": {"type": "string"},
                "stack": {
                    "type": "object",
                    "properties": {
                        "frontend": {"type": "object", "properties": {"framework": {"type": "string"}, "language": {"type": "string"}}, "required": ["framework", "language"]},
                        "backend": {"type": "object", "properties": {"framework": {"type": "string"}, "language": {"type": "string"}}, "required": ["framework", "language"]},
                        "database": {"type": "string"},
                    },
                    "required": ["frontend", "backend", "database"],
                },
                "deployment": {"type": "string"},
            },
            "required": ["app_name", "stack", "deployment"],
        },
    },
    "enum_constraints": {
        "prompt": "分析文本情感：'今天天气真好，我很开心'",
        "schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "sentiment": {"type": "string", "enum": ["positive", "negative", "neutral"]},
                "confidence": {"type": "number"},
                "keywords": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["text", "sentiment", "confidence", "keywords"],
        },
    },
}


@router.post("/api/test/structured-output")
async def test_structured_output(req: StructuredTestRequest):
    """测试模型结构化输出能力"""
    test_def = _STRUCTURED_TEST_SCHEMAS.get(req.schema_level)
    if not test_def:
        raise HTTPException(status_code=400, detail=f"不支持的 schema_level: {req.schema_level}，可选: {list(_STRUCTURED_TEST_SCHEMAS.keys())}")

    enabled_models = _deps.config_manager.get_enabled_models()
    if not enabled_models:
        raise HTTPException(status_code=503, detail="没有可用模型")

    if req.model:
        target_models = [(prov, mc.name) for prov, mc in enabled_models if mc.name == req.model]
        if not target_models:
            raise HTTPException(status_code=404, detail=f"模型 {req.model} 未找到或未启用")
    else:
        target_models = [(prov, mc.name) for prov, mc in enabled_models]

    results = []
    for provider_name, model_id in target_models:
        result = await _test_single_model_structured(provider_name, model_id, test_def["prompt"], test_def["schema"])
        results.append(result)

    passed = [r for r in results if r["json_valid"] and r["schema_valid"]]
    return {"schema_level": req.schema_level, "total_tested": len(results), "passed": len(passed), "failed": len(results) - len(passed), "results": results}


async def _test_single_model_structured(provider_name: str, model_id: str, prompt: str, schema: dict) -> dict:
    """测试单个模型的结构化输出"""
    from src.models.schemas import ResponseFormat

    schema_desc = json.dumps(schema, ensure_ascii=False, indent=2)
    full_prompt = f"{prompt}\n\n请严格按以下 JSON Schema 输出：\n{schema_desc}"

    request = ChatCompletionRequest(
        model=model_id,
        messages=[ChatMessage(role="user", content=full_prompt)],
        temperature=0.1, max_tokens=500,
        response_format=ResponseFormat(type="json_schema", json_schema={"name": "test_output", "schema": schema}),
    )

    start = time.time()
    try:
        enabled_models = _deps.config_manager.get_enabled_models()
        _prov, _model, result = await _deps.dispatcher.dispatch(request, enabled_models, trace_id=uuid.uuid4().hex[:8])
        latency = round((time.time() - start) * 1000)
        content = result.choices[0].message.content or "" if result.choices else ""
        content = _extract_json_from_response(content)
        try:
            parsed = json.loads(content)
            required = schema.get("required", [])
            has_all_fields = all(k in parsed for k in required)
            return {"model": model_id, "provider": provider_name, "status": "success", "json_valid": True, "schema_valid": has_all_fields, "latency_ms": latency, "output": content[:200]}
        except (json.JSONDecodeError, ValueError) as e:
            return {"model": model_id, "provider": provider_name, "status": "json_parse_error", "json_valid": False, "schema_valid": False, "latency_ms": latency, "error": str(e)[:60], "raw_output": content[:200]}
    except Exception as e:
        latency = round((time.time() - start) * 1000)
        return {"model": model_id, "provider": provider_name, "status": "error", "json_valid": False, "schema_valid": False, "latency_ms": latency, "error": str(e)[:100]}


@router.get("/api/test/structured-output/schemas")
async def list_test_schemas():
    """列出可用的测试 schema"""
    return {name: {"prompt": v["prompt"], "required_fields": v["schema"].get("required", [])} for name, v in _STRUCTURED_TEST_SCHEMAS.items()}
