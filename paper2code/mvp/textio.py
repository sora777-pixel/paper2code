"""LLM 回复解析、路径安全、结果键工具。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

FILE_RE = re.compile(r"<<<FILE:\s*(?P<path>[^>\n]+?)\s*>>>\n(?P<body>.*?)\n?<<<END>>>", re.S)


def slug(text: str, default: str = "x") -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", str(text).lower()).strip("_")
    return s or default


def safe_rel(p: str) -> str:
    p = p.strip().strip("`").replace("\\", "/")
    if not p or p.startswith("/") or ".." in Path(p).parts or ":" in p:
        raise ValueError("unsafe path from LLM: %r" % p)
    return p


def strip_fence(body: str) -> str:
    s = body.strip("\n")
    m = re.match(r"^```[A-Za-z0-9_+-]*\n(.*)\n```\s*$", s, re.S)
    return m.group(1) if m else s


def parse_files(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for m in FILE_RE.finditer(text or ""):
        out[safe_rel(m.group("path"))] = strip_fence(m.group("body")).rstrip() + "\n"
    return out


def extract_json(text: str) -> Any:
    """取 LLM 回复里的第一个 JSON 对象（裸 JSON / 围栏 / 夹在文字中间都行）。"""
    s = (text or "").strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    start = s.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start:i + 1])
                    except Exception:
                        break
        start = s.find("{", start + 1)
    raise ValueError("回复中没有可解析的 JSON 对象")


def get_path(d: Any, dotted: str) -> Any:
    """解析 'a.b.0.c'；缺失时抛 KeyError / IndexError / ValueError。"""
    cur = d
    for part in dotted.split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur[part]
        else:
            raise KeyError(dotted)
    return cur


def describe_results(d: Any, prefix: str = "", out: Optional[List[str]] = None) -> List[str]:
    """把 results 展平成「路径: 类型/样例」行，供提示词引用真实存在的键。"""
    out = [] if out is None else out
    if isinstance(d, dict):
        for k, v in d.items():
            describe_results(v, "%s%s." % (prefix, k), out)
    elif isinstance(d, list):
        out.append("%s: list(len=%d) e.g. %s" % (prefix[:-1], len(d), d[:3]))
    else:
        out.append("%s: %s = %s" % (prefix[:-1], type(d).__name__, d))
    return out
