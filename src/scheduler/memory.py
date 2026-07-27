# Created by model-proxy on 2026/05/20
# Copyright © 2026

"""
Agent Memory System — 向后兼容入口

实现已迁移到 memory_pkg/ 子包。此文件保留以确保现有 import 正常工作。
"""

from src.scheduler.memory_pkg import (  # noqa: F401
    EntityGraph,
    EntityRelation,
    LongTermMemoryStore,
    MemoryConsolidator,
    MemoryConstitution,
    MemoryEntry,
    MemoryManager,
    ProceduralMemoryEntry,
    ProceduralMemoryStore,
    SessionMemoryExtractor,
    SessionMemoryStore,
)
from src.scheduler.memory_pkg._impl import get_memory_manager  # noqa: F401

__all__ = [
    "EntityGraph",
    "EntityRelation",
    "LongTermMemoryStore",
    "MemoryConsolidator",
    "MemoryConstitution",
    "MemoryEntry",
    "MemoryManager",
    "ProceduralMemoryEntry",
    "ProceduralMemoryStore",
    "SessionMemoryExtractor",
    "SessionMemoryStore",
    "get_memory_manager",
]
