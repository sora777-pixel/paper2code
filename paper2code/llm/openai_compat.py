"""OpenAI 兼容 Provider。

只要服务端实现了 ``POST {base_url}/chat/completions``，就能直接接入：
OpenAI、DeepSeek、Moonshot、通义、vLLM、Ollama(``/v1``)、LM Studio 等。

刻意只用 stdlib ``urllib`` 发请求，避免为一个可选依赖把 MVP 装重。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from ..models import Algorithm, Paper, Slide, Table
from .base import LLMProvider


def _title_bits(paper: Paper) -> List[str]:
    from ..explain.courseware import title_bits

    return title_bits(paper)


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self.base_url = str(self.config.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        self.api_key = str(self.config.get("api_key") or "")
        self.model = str(self.config.get("model") or "gpt-4o-mini")
        self.timeout = int(self.config.get("timeout") or 120)
        self.temperature = float(self.config.get("temperature") or 0.3)

    def available(self) -> bool:
        return bool(self.api_key)

    def describe(self) -> str:
        return f"openai-compatible({self.model} @ {self.base_url})"

    # ------------------------------------------------------------------ #
    def _chat(self, system: str, user: str, json_mode: bool = False) -> str:
        if not self.available():
            raise RuntimeError("未配置 API Key")
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as fh:  # noqa: S310
                data = json.loads(fh.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "ignore")[:400]
            raise RuntimeError(f"LLM HTTP {exc.code}: {body}") from exc
        except Exception as exc:
            raise RuntimeError(f"LLM 调用失败：{exc}") from exc

        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"LLM 返回结构异常：{str(data)[:300]}") from exc

    @staticmethod
    def _json_block(text: str) -> Any:
        text = text.strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
        try:
            return json.loads(text)
        except Exception:
            m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(1))
                except Exception:
                    return None
        return None

    # -- 上下文摘要 ------------------------------------------------------- #
    def _paper_digest(self, paper: Paper, char_budget: int = 9000) -> str:
        parts = [
            f"# 标题\n{paper.title}",
            f"# 作者\n{', '.join(paper.authors)}",
            f"# 摘要\n{paper.abstract}",
        ]
        for s in paper.body_sections():
            parts.append(f"# {s.heading}\n{s.text}")
        if paper.algorithms:
            for a in paper.algorithms:
                parts.append(f"# 伪代码 {a.id}（{a.caption}）\n" + "\n".join(a.lines))
        if paper.tables:
            for t in paper.tables[:3]:
                head = " | ".join(t.header)
                rows = "\n".join(" | ".join(r) for r in t.rows[:12])
                parts.append(f"# 表格 {t.id}（{t.caption}）\n{head}\n{rows}")
        digest = "\n\n".join(p for p in parts if p.strip())
        return digest[:char_budget]

    # ------------------------------------------------------------------ #
    def outline(self, paper: Paper, target_slides: int = 12) -> List[str]:
        sys = (
            "你是一位善于把技术论文讲清楚的资深讲师。你的任务是设计一份"
            "面向「学习者」的讲解大纲，而不是照抄论文目录。大纲要有教学叙事："
            "从问题动机 → 核心思想 → 方法 → 证据 → 局限 → 复现路径。"
            "只输出 JSON：{\"outline\": [\"小节标题\", ...]}，共 "
            f"{target_slides} 节左右，标题用中文、简短。"
        )
        try:
            out = self._json_block(self._chat(sys, self._paper_digest(paper), json_mode=True))
        except Exception as exc:
            self._warn(f"LLM outline call failed, offline fallback: {exc}")
            from .offline import OfflineProvider
            return OfflineProvider().outline(paper, target_slides)
        if isinstance(out, dict) and isinstance(out.get("outline"), list):
            items = [str(x).strip() for x in out["outline"] if str(x).strip()]
            if items:
                return items
        self._warn("LLM 大纲解析失败，已回退到离线大纲")
        from .offline import OfflineProvider

        return OfflineProvider().outline(paper, target_slides)

    def slides(self, paper: Paper, outline: List[str]) -> List[Slide]:
        sys = (
            "你是技术课程讲师。根据给定论文内容，为大纲中的每一节生成幻灯片。"
            "要求：bullets 每条不超过 28 个汉字，必须来自论文事实，不得编造数字。"
            "输出 JSON：{\"slides\":[{\"title\":\"...\",\"bullets\":[\"...\"],\"notes\":\"讲稿补充\"}]}，"
            f"slides 数量必须等于 {len(outline)}。"
        )
        user = f"大纲：\n" + "\n".join(f"{i+1}. {h}" for i, h in enumerate(outline)) + "\n\n" + self._paper_digest(paper)
        try:
            data = self._json_block(self._chat(sys, user, json_mode=True))
        except Exception as exc:
            self._warn(f"LLM slides call failed, offline fallback: {exc}")
            from .offline import OfflineProvider
            return OfflineProvider().slides(paper, outline)

        slides: List[Slide] = []
        if isinstance(data, dict) and isinstance(data.get("slides"), list):
            for item in data["slides"]:
                if not isinstance(item, dict):
                    continue
                slides.append(
                    Slide(
                        title=str(item.get("title") or "").strip() or "未命名",
                        bullets=[str(b).strip() for b in (item.get("bullets") or []) if str(b).strip()][:6],
                        notes=str(item.get("notes") or "").strip(),
                    )
                )
        if len(slides) < max(3, len(outline) // 2):
            self._warn("LLM 幻灯片解析不完整，已回退到离线生成")
            from .offline import OfflineProvider

            return OfflineProvider().slides(paper, outline)

        head = Slide(
            title=paper.title or "论文讲解",
            bullets=_title_bits(paper),
            kind="title",
        )
        agenda = Slide(title="本次讲解路线", bullets=[f"{i+1}. {h}" for i, h in enumerate(outline)], kind="agenda")
        from ..explain.courseware import closing_bullets

        tail = Slide(title="结论、展望与不足", bullets=closing_bullets(paper), kind="takeaway")
        return [head, agenda] + slides + [tail]

    def dialogue(self, paper: Paper) -> List[Dict[str, str]]:
        sys = (
            "你是中文「论文写作导读」播客编剧。两位角色全程使用中文："
            "A=主持人（提问、串联），B=熟悉稿件的作者向导（回答）。"
            "目标：帮助作者/读者理清「研究目的、行文思路、实验设计、结论、不足或改进」。"
            "只用 Introduction / Methods / Results / Discussion（含结论）相关信息。"
            "严禁讲解公式推导、符号含义或图表读法；不要逐表逐图点评。"
            "口语、结构化叙事，不要公式问答。输出 JSON："
            "{\"turns\":[{\"speaker\":\"A\",\"text\":\"...\"}]}。"
            "建议 12-20 轮，总字数 1000-1800 字，必须全中文。"
        )
        try:
            data = self._json_block(self._chat(sys, self._paper_digest(paper), json_mode=True))
        except Exception as exc:
            self._warn(f"LLM dialogue call failed, offline fallback: {exc}")
            from .offline import OfflineProvider
            return OfflineProvider().dialogue(paper)
        if isinstance(data, dict) and isinstance(data.get("turns"), list):
            turns = []
            for t in data["turns"]:
                if not isinstance(t, dict):
                    continue
                spk = str(t.get("speaker") or "A").strip().upper()
                txt = str(t.get("text") or "").strip()
                if txt:
                    turns.append({"speaker": "A" if spk.startswith("A") else "B", "text": txt})
            if len(turns) >= 6:
                return turns
        self._warn("LLM 播客稿生成失败，已回退到离线对白")
        from .offline import OfflineProvider

        return OfflineProvider().dialogue(paper)

    def pseudocode_to_python(self, algo: Algorithm, context: str = "") -> str:
        sys = (
            "你是代码复现工程师。把论文中的伪代码翻译成**可直接运行的 Python**。"
            "要求：\n"
            "1) 保持原算法逻辑，不得增删步骤；\n"
            "2) 只使用 Python 标准库（math/random/statistics/itertools/collections）；\n"
            "3) 函数签名要接受输入数据；\n"
            "4) 用中文注释标注每一步对应的伪代码行；\n"
            "5) 只输出代码，不要解释，不要 Markdown 代码围栏。"
        )
        user = f"算法标题：{algo.caption}\n\n伪代码：\n{algo.source}\n\n论文上下文：\n{context[:3000]}"
        code = self._chat(sys, user).strip()
        code = re.sub(r"^```(?:python)?\s*|\s*```$", "", code, flags=re.MULTILINE).strip()
        if "def " not in code:
            self._warn("LLM 伪代码翻译结果可疑，已回退到规则翻译")
            from .offline import OfflineProvider

            return OfflineProvider().pseudocode_to_python(algo, context)
        return code

    def repro_strategy(self, paper: Paper, mode: str, targets: List[Dict[str, Any]]) -> str:
        sys = (
            "你是复现工程师，需要写一段简明的复现策略说明（150-300 字）。"
            "说明：拿到该论文后，在单机、小数据（<1 万条记录）条件下，"
            "应该按什么顺序、用什么方式重现论文中的表格与图表，以及主要的失败风险。"
            "只输出正文，不要标题。"
        )
        user = (
            f"论文：{paper.title}\n情形：{mode}\n复现目标：{json.dumps(targets, ensure_ascii=False)[:1500]}\n\n"
            + self._paper_digest(paper, char_budget=4000)
        )
        try:
            return self._chat(sys, user).strip()
        except Exception as exc:
            self._warn(f"策略生成失败：{exc}")
            return ""

    def explain_table(self, table: Table) -> str:
        head = " | ".join(table.header)
        rows = "\n".join(" | ".join(r) for r in table.rows[:15])
        try:
            return self._chat(
                "用一句话（不超过 60 字）指出这张表格中最值得注意的结果，必须引用具体数字。",
                f"表 {table.id} {table.caption}\n{head}\n{rows}",
            ).strip()
        except Exception:
            return ""
