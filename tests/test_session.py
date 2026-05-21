# Created by model-proxy on 2026/05/17
# Copyright © 2026

"""SessionManager 和 ChatSession 单元测试"""

import pytest

from src.scheduler.session import ChatSession, SessionManager


class TestChatSession:
    def test_create_default(self):
        s = ChatSession()
        assert s.title == "新对话"
        assert s.model == "auto"
        assert len(s.messages) == 0

    def test_add_message(self):
        s = ChatSession()
        s.add_message("user", "hello")
        assert len(s.messages) == 1
        assert s.messages[0]["role"] == "user"
        assert s.messages[0]["content"] == "hello"

    def test_auto_title(self):
        s = ChatSession()
        s.add_message("user", "什么是机器学习")
        s.auto_title()
        assert "机器学习" in s.title

    def test_context_messages_include_system(self):
        s = ChatSession(max_context_tokens=32000)
        s.add_message("user", "hi")
        ctx = s.get_context_messages()
        assert ctx[0]["role"] == "system"
        assert len(ctx) == 2

    def test_context_truncation(self):
        s = ChatSession(max_context_tokens=100)
        for i in range(50):
            s.add_message("user", f"消息 {i} " * 20)
        ctx = s.get_context_messages()
        assert len(ctx) < 52

    def test_to_dict_and_from_dict(self):
        s = ChatSession(title="测试会话", model="gemini")
        s.add_message("user", "hello")
        s.add_message("assistant", "hi there")

        data = s.to_dict()
        s2 = ChatSession.from_dict(data)
        assert s2.id == s.id
        assert s2.title == s.title
        assert s2.model == s.model
        assert len(s2.messages) == 2

    def test_max_context_tokens_passed(self):
        s = ChatSession(max_context_tokens=500)
        assert s._max_context_tokens == 500


class TestSessionManager:
    def test_create_and_get(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        mgr = SessionManager(max_context_tokens=4000, max_sessions=10)
        session = mgr.create(title="test")
        assert mgr.get(session.id) is not None

    def test_delete(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        mgr = SessionManager()
        session = mgr.create()
        assert mgr.delete(session.id) is True
        assert mgr.get(session.id) is None

    def test_max_sessions_eviction(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        mgr = SessionManager(max_sessions=2)
        s1 = mgr.create(title="first")
        s2 = mgr.create(title="second")
        s3 = mgr.create(title="third")
        assert mgr.get(s1.id) is None
        assert mgr.get(s2.id) is not None
        assert mgr.get(s3.id) is not None

    def test_list_sessions(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        mgr = SessionManager()
        mgr.create(title="A")
        mgr.create(title="B")
        sessions = mgr.list_sessions()
        assert len(sessions) == 2

    def test_rename(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        mgr = SessionManager()
        s = mgr.create(title="old")
        assert mgr.rename(s.id, "new") is True
        assert mgr.get(s.id).title == "new"

    def test_zero_max_sessions_means_unlimited(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        mgr = SessionManager(max_sessions=0)
        for i in range(10):
            mgr.create(title=f"session-{i}")
        assert len(mgr.list_sessions()) == 10
