# Created by model-proxy on 2026/05/17
# Copyright © 2026

"""从模型回复中分离思考过程和最终回复。

支持：
1. <think>/<thinking> 标签
2. Gemma 式 bullet-point 推理 + 末尾回复
3. 元认知文本（Final Polish/Wait/Since the user 等）+ 末尾回复
"""

from __future__ import annotations

import re


_THINK_TAG_RE = re.compile(r"<think(?:ing)?>(.*?)</think(?:ing)?>", re.DOTALL)

_THINKING_LINE_PATTERNS = [
    re.compile(r"^\s*[*•]\s"),
    re.compile(r"^\s{4,}\S"),
    re.compile(r"^\s*\("),
    re.compile(r"^Final\s+(Polish|Answer|Response|Draft)\s*:", re.I),
    re.compile(
        r"^\s*(Wait|Hmm|Let me|I should|I need to|I will|Since the|However,|Actually,|"
        r"The user|User input|Task:|Constraints?:|Draft|Revised|Refinement)",
        re.I,
    ),
]


def _is_thinking_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return any(p.match(line) for p in _THINKING_LINE_PATTERNS)


def strip_thinking(text: str) -> tuple[str, str]:
    """从模型回复中分离思考过程和最终回复。

    返回 (clean_reply, thinking_content)。
    """
    thinking_parts = _THINK_TAG_RE.findall(text)
    if thinking_parts:
        clean = _THINK_TAG_RE.sub("", text).strip()
        thinking = "\n---\n".join(p.strip() for p in thinking_parts if p.strip())
        return clean, thinking

    lines = text.strip().split("\n")
    if len(lines) < 3:
        return text, ""

    non_empty = [(i, lines[i]) for i in range(len(lines)) if lines[i].strip()]
    if len(non_empty) < 3:
        return text, ""

    thinking_count = sum(1 for _, l in non_empty if _is_thinking_line(l))

    if thinking_count < 2 or thinking_count < len(non_empty) * 0.5:
        return text, ""

    last_clean_start = -1
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip()
        if not stripped:
            continue
        if _is_thinking_line(lines[i]):
            break
        last_clean_start = i

    if last_clean_start > 0:
        clean = "\n".join(lines[last_clean_start:]).strip()
        thinking = "\n".join(lines[:last_clean_start]).strip()
        if clean:
            return _dedup_answer(clean), thinking

    paragraphs = re.split(r"\n\s*\n", text.strip())
    if len(paragraphs) >= 2:
        last_para = paragraphs[-1].strip()
        if last_para and not _is_thinking_line(last_para.split("\n")[0]):
            thinking_text = "\n\n".join(paragraphs[:-1]).strip()
            return _dedup_answer(last_para), thinking_text

    cn_answer = _extract_chinese_answer(text)
    if cn_answer:
        thinking_text = text[:text.rfind(cn_answer)].strip()
        return _dedup_answer(cn_answer), thinking_text

    trailing = _extract_trailing_answer(text)
    if trailing:
        thinking_text = text[:text.rfind(trailing)].strip()
        return _dedup_answer(trailing), thinking_text

    return text, ""


def _dedup_answer(text: str) -> str:
    """去除 Gemma 模型常见的答案重复（先引号内草稿，后直接输出）。"""
    stripped = text.strip()
    for q_open, q_close in [('"', '"'), ('\u201c', '\u201d'), ("'", "'"), ('\u2018', '\u2019')]:
        if stripped.startswith(q_open) and q_close in stripped[1:]:
            end_idx = stripped.index(q_close, 1) + len(q_close)
            quoted = stripped[len(q_open):end_idx - len(q_close)].strip()
            rest = stripped[end_idx:].strip()
            if rest and _similar(quoted, rest):
                return rest
    half = len(stripped) // 2
    if half > 2:
        first_half = stripped[:half].strip()
        second_half = stripped[half:].strip()
        if _similar(first_half, second_half):
            return second_half
    return stripped


def _similar(a: str, b: str) -> bool:
    a_clean = re.sub(r"[\s，。！？、\u201c\u201d\u2018\u2019\"'.,]", "", a)
    b_clean = re.sub(r"[\s，。！？、\u201c\u201d\u2018\u2019\"'.,]", "", b)
    if not a_clean or not b_clean:
        return False
    shorter = min(len(a_clean), len(b_clean))
    longer = max(len(a_clean), len(b_clean))
    if shorter < 3:
        return a_clean == b_clean
    common = sum(1 for ca, cb in zip(a_clean, b_clean) if ca == cb)
    return common / longer > 0.8


_CHINESE_SENTENCE_RE = re.compile(
    r"([\u4e00-\u9fff][\u4e00-\u9fff\s，。！？、（）\u201c\u201d\u2018\u2019：；…·\w A-Za-z0-9\-.]{2,}[。！？\u201d]?)$"
)


def _extract_chinese_answer(text: str) -> str:
    """提取文本末尾的中文句子作为最终答案。"""
    m = _CHINESE_SENTENCE_RE.search(text.strip())
    if m:
        candidate = m.group(1).strip()
        if candidate and len(candidate) < len(text) * 0.5:
            return candidate
    return ""


def _extract_trailing_answer(text: str) -> str:
    """从全是思考内容的文本末尾提取被拼接的最终答案。"""
    lines = text.strip().split("\n")
    last_line = ""
    for line in reversed(lines):
        if line.strip():
            last_line = line
            break
    if not last_line:
        return ""

    content = re.sub(r"^\s*[*•]\s+", "", last_line).strip()
    if not content:
        return ""

    for pattern in [
        re.compile(r'[.。!！?？)\）"\u201d]\s*(.+)$'),
        re.compile(r'(?:best|better|correct|answer|回复|答案|直接)[.。"\u201d)）]*\s*(.+)$', re.I),
    ]:
        m = pattern.search(content)
        if m:
            candidate = m.group(1).strip()
            if candidate and 0 < len(candidate) < len(content):
                return candidate

    all_thinking = all(
        _is_thinking_line(l) for l in lines if l.strip()
    )
    if all_thinking and len(content) < 200:
        return content

    return ""
