# Created by model-proxy on 2026/05/21
# Copyright © 2026

"""轻量级 Tracing 模块

提供 span 创建和记录，不强制依赖 OpenTelemetry。
当 opentelemetry SDK 可用时自动使用；否则退化为日志记录。
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_otel_tracer = None

try:
    from opentelemetry import trace
    from opentelemetry.trace import StatusCode
    _otel_tracer = trace.get_tracer("model-proxy")
    logger.info("OpenTelemetry tracer 已加载")
except ImportError:
    pass


@dataclass
class SpanRecord:
    """本地 span 记录（用于无 otel 环境下的调试）"""
    name: str
    trace_id: str
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"
    error: str = ""

    @property
    def duration_ms(self) -> float:
        return (self.end_time - self.start_time) * 1000


_recent_spans: list[SpanRecord] = []
_SPAN_BUFFER_MAX = 200


@contextmanager
def trace_span(name: str, trace_id: str = "", **attributes):
    """创建一个 tracing span

    使用方式:
        with trace_span("dispatch", trace_id=tid, model=model_name) as span:
            result = await do_work()
            span["tokens"] = result.usage.total_tokens
    """
    record = SpanRecord(name=name, trace_id=trace_id, attributes=dict(attributes))
    mutable_attrs: dict[str, Any] = {}

    if _otel_tracer:
        with _otel_tracer.start_as_current_span(name) as otel_span:
            otel_span.set_attribute("trace_id", trace_id)
            for k, v in attributes.items():
                otel_span.set_attribute(k, str(v) if not isinstance(v, (int, float, bool)) else v)
            try:
                yield mutable_attrs
                for k, v in mutable_attrs.items():
                    otel_span.set_attribute(k, str(v) if not isinstance(v, (int, float, bool)) else v)
            except Exception as e:
                otel_span.set_status(StatusCode.ERROR, str(e))
                record.status = "error"
                record.error = str(e)[:200]
                raise
            finally:
                record.end_time = time.time()
                record.attributes.update(mutable_attrs)
    else:
        try:
            yield mutable_attrs
        except Exception as e:
            record.status = "error"
            record.error = str(e)[:200]
            raise
        finally:
            record.end_time = time.time()
            record.attributes.update(mutable_attrs)

    if len(_recent_spans) >= _SPAN_BUFFER_MAX:
        _recent_spans.pop(0)
    _recent_spans.append(record)


def get_recent_spans(limit: int = 50) -> list[dict[str, Any]]:
    """获取最近的 span 记录（用于调试 API）"""
    spans = _recent_spans[-limit:]
    return [
        {
            "name": s.name,
            "trace_id": s.trace_id,
            "duration_ms": round(s.duration_ms, 1),
            "status": s.status,
            "attributes": s.attributes,
            "error": s.error or None,
        }
        for s in reversed(spans)
    ]


def is_otel_available() -> bool:
    """检查 OpenTelemetry 是否可用"""
    return _otel_tracer is not None
