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
from ..textutil import condense, split_sentences
from .base import LLMProvider


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

        # 成本护栏（M1-⑥）：把这次调用的用量记到本 Provider 上。
        # 端点的 usage 字段缺失时按 0 记（只影响记账精度，不影响拦截次数/耗时上限）。
        u = data.get("usage") or {}
        try:
            self.usage.record(int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0))
        except (TypeError, ValueError):
            self.usage.record()

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

    # MVP-PATCH:llm-openai
    def complete(self, system: str, user: str, stage: str = "") -> str:
        """自由文本补全（MVP 生成用）：代码生成比讲解慢，超时放宽到至少 300s；失败直接抛出，由 mvp 层降级。"""
        self.guard_budget(stage)   # 成本护栏（M1-⑥）：超限即抛，由 mvp 层降级为离线脚手架
        old = self.timeout
        self.timeout = max(old, 300)
        try:
            return self._chat(system, user)
        finally:
            self.timeout = old

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
            "你是一位善于把技术论文讲清楚的资深讲师。你的任务是设计一份面向学习者的讲解大纲，"
            "而不是照抄论文目录。大纲要有一条清晰的教学叙事："
            "为什么做（问题与动机）→ 怎么做（核心思想与方法）→ 做得怎样（证据与结果）→ "
            "还有什么不足（局限与边界）→ 如何复现（可执行路径）。"
            "全部使用简体中文；每个标题都要让读者一眼看出这一节要回答的问题，"
            "避免直译腔、名词堆砌和空泛口号。"
            "只输出 JSON：{\"outline\": [\"小节标题\", ...]}，共 "
            f"{target_slides} 节左右。"
        )
        out = self._json_block(self._chat(sys, self._paper_digest(paper), json_mode=True))
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
            "全部使用简体中文；论文专有名词和必要缩写可以保留，但首次出现时要用一句中文解释。"
            "每条 bullet 必须是完整、通顺的一句话，不要只堆名词；"
            "要用「因为、所以、但是、例如、这意味着」等连接词把因果、转折和例子说清楚。"
            "每条不超过 32 个汉字，必须来自论文事实，不得编造数字。"
            "notes 写成 2-3 句口语讲解，说明这一页为什么重要、听众最容易误解什么。"
            "输出 JSON：{\"summary\":\"一句话中文概括（不超过 60 字）\","
            "\"slides\":[{\"title\":\"...\",\"bullets\":[\"...\"],\"notes\":\"讲稿补充\"}]}，"
            f"slides 数量必须等于 {len(outline)}。"
        )
        user = f"大纲：\n" + "\n".join(f"{i+1}. {h}" for i, h in enumerate(outline)) + "\n\n" + self._paper_digest(paper)
        data = self._json_block(self._chat(sys, user, json_mode=True))

        slides: List[Slide] = []
        summary = ""
        if isinstance(data, dict):
            summary = str(data.get("summary") or "").strip()
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

        # 封面与目录由本地补齐，保证版式一致
        # 封面第二行优先用 LLM 写的中文一句话概括；没有中文时再退回摘要，
        # 避免封面直接出现一整段英文摘要。
        if len(re.findall(r"[\u4e00-\u9fff]", summary)) >= 6:
            headline = summary
        else:
            headline = "本文提出问题、方法、结果与复现路径，下面按教学叙事逐步展开。"
        head = Slide(title=paper.title or "论文讲解",
                     bullets=[", ".join(paper.authors[:4]), headline],
                     kind="title")
        agenda = Slide(title="本次讲解路线", bullets=[f"{i+1}. {h}" for i, h in enumerate(outline)], kind="agenda")
        tail = Slide(
            title="总结：要点回顾与复现路径",
            bullets=self._summary_from_llm(paper, slides, outline) or self._summary_bullets(paper, slides, outline),
            kind="takeaway",
            notes="用五句话收束全篇：问题、方法、结果、局限与复现路径；数字均来自论文或前文幻灯片。",
        )
        return [head, agenda] + slides + [tail]

    def _summary_from_llm(self, paper: Paper, slides: List[Slide], outline: List[str]) -> List[str]:
        """让 LLM 生成结构化的总结页；失败或格式不合规时返回空列表，由规则汇总兜底。

        为什么要单独一次调用：正文页只按大纲逐节展开，天然缺少「把全篇收拢」的动作。
        这里要求 LLM 按「问题 → 方法 → 结果 → 局限 → 复现」的固定叙事写 4-5 条，
        每条带中文标签，既方便阅读，也方便 TTS 在播报时做语气切换。
        """
        sys = (
            "你是课程总结设计师。请根据论文和已经生成的幻灯片，写 4-5 条总结。"
            "每条必须以「问题：」「方法：」「结果：」「局限：」「复现：」之一开头，并按这个顺序排列，"
            "至少覆盖问题、方法、结果、复现四类。"
            "每条 30-50 个汉字，必须具体：结果条要写出关键数字或结论，复现条要给出可执行入口；"
            "不要照抄幻灯片原句，要用总结口吻把因果关系说清楚；全部使用简体中文。"
            "只输出 JSON：{\"bullets\":[\"问题：...\", ...]}"
        )
        slide_lines = []
        for s in slides:
            if s.bullets:
                slide_lines.append("- %s：%s" % (s.title, "；".join(s.bullets[:3])))
        repo = next((c.url for c in paper.code_refs if c.kind == "repo" and c.url), "")
        user = (
            "大纲：\n" + "\n".join(f"{i+1}. {h}" for i, h in enumerate(outline))
            + "\n\n幻灯片要点：\n" + "\n".join(slide_lines[:20])
            + "\n\n摘要：\n" + condense(paper.abstract, 800)
            + ("\n\n复现入口：" + repo if repo else "\n\n复现入口：论文未给出自有代码仓库")
        )
        try:
            self.guard_budget("summary")
            data = self._json_block(self._chat(sys, user, json_mode=True))
        except Exception as exc:
            self._warn("总结页 LLM 生成失败，已回退到规则汇总：%s" % exc)
            return []

        raw: List[str] = []
        if isinstance(data, dict) and isinstance(data.get("bullets"), list):
            for item in data["bullets"]:
                text = str(item).strip()
                if text:
                    raw.append(text[:140])
        labels = ("问题", "方法", "结果", "局限", "复现")
        cleaned: List[str] = []
        for text in raw:
            # 统一中英文冒号，避免「问题:」混进来
            text = re.sub(r"^(问题|方法|结果|局限|复现)\s*[:：]\s*", r"\1：", text)
            if any(text.startswith(label + "：") for label in labels):
                cleaned.append(text)
        if len(cleaned) < 3:
            self._warn("总结页 LLM 输出缺少「问题/方法/结果/局限/复现」标签，已回退到规则汇总")
            return []
        return cleaned[:6]

    def _summary_bullets(self, paper: Paper, slides: List[Slide], outline: List[str]) -> List[str]:
        """规则汇总兜底：按「问题 → 方法 → 结果 → 局限 → 复现」抽取并加标签。

        LLM 只被要求生成与大纲等量的正文页，不会额外生成结论页；这里保证最后一页
        至少有 4 条、最多 6 条，并且每条都带中文标签，逻辑顺序固定。
        """
        picks: List[str] = []
        used: set = set()

        def first_bullet(*keys: str) -> str:
            # 按关键词优先级找，而不是按幻灯片顺序：否则「已有机器学习方法」
            # 会抢在「核心思路」前面，把方法页当成小结。
            # 同一个页面只能贡献一条，避免「问题」和「方法」抓到同一句。
            for key in keys:
                for s in slides:
                    if id(s) in used:
                        continue
                    if s.bullets and key in s.title:
                        used.add(id(s))
                        return s.bullets[0].strip()
            return ""

        groups = (
            ("问题", ("背景", "动机", "挑战", "为什么", "问题")),
            ("方法", ("核心", "框架", "方法", "思路")),
            ("结果", ("主结果", "结果", "证据", "对比", "实验")),
            ("局限", ("局限", "边界", "适用", "不足")),
        )
        for label, keys in groups:
            b = first_bullet(*keys)
            if b:
                picks.append(f"{label}：{b}")

        if len(picks) < 3:
            text = paper.section_text("conclusion", "discussion") or paper.abstract or ""
            for sent in split_sentences(text):
                b = condense(sent, 64)
                if b and all(b not in p for p in picks):
                    picks.append(f"补充：{b}")
                if len(picks) >= 3:
                    break

        if not picks:
            picks = [f"问题：{condense(paper.title, 60)}"]
        repo = next((c.url for c in paper.code_refs if c.kind == "repo" and c.url), "")
        entry = f"复现：{repo}" if repo else "复现：见 TECH_GUIDE 与 repro_run.sh"
        if entry not in picks:
            picks.append(entry)
        # 最少 4 条：不足时补在「复现」之前，保持问题→方法→结果→局限→复现的顺序
        if len(picks) < 4:
            extra = "结果：关键数字与对照表见正文结果页，复现时逐项核对"
            if picks and picks[-1].startswith("复现"):
                picks.insert(len(picks) - 1, extra)
            else:
                picks.append(extra)
        return picks[:6]

    def dialogue(self, paper: Paper) -> List[Dict[str, str]]:
        sys = (
            "你是一档中文技术播客的编剧。两位主播：A 是好奇的主持人，负责提问、追问、打比方，"
            "也会表达惊讶和恍然大悟；B 是熟悉这篇论文的研究者，负责解释、澄清、举例和总结。"
            "要求：全中文口语；除论文专有名词和必要缩写外，不出现英文单词。"
            "对话要有自然的情绪起伏和语调变化：A 可以惊讶、追问、反问、总结，"
            "B 可以先给结论再解释、用对比或例子说明、强调关键数字；"
            "多用「嗯」「对」「其实」「说白了」「换句话说」「这里要注意」这类口语词。"
            "每轮 1-3 句、句子要短（≤40 字），禁止书面长句与 Markdown/符号；"
            "每 3-4 轮至少出现一次明确的语气变化（疑问、惊讶、强调或总结），避免平铺直叙；"
            "全篇至少安排 2 处感叹或强调句（以「！」结尾），让朗读有起伏；"
            "A 的追问要接住 B 的上一句，不能自说自话；问句以「？」结尾；"
            "所有数字与结论必须来自论文原文，禁止编造。"
            "输出 JSON：{\"turns\":[{\"speaker\":\"A\",\"text\":\"...\"}]}，"
            "总轮次 18-26 轮，总字数 1200-2000 字。"
        )
        data = self._json_block(self._chat(sys, self._paper_digest(paper), json_mode=True))
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
        self._warn("LLM 播客稿解析失败，已回退到离线对白")
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
            "全部使用简体中文，逻辑按「先做什么、再做什么、最后核对什么」展开。"
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
                "用一句简体中文（不超过 60 字）指出这张表格中最值得注意的结果，必须引用具体数字。",
                f"表 {table.id} {table.caption}\n{head}\n{rows}",
            ).strip()
        except Exception:
            return ""
