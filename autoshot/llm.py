"""LLM 决策策略（P1）：启发式穷尽后，用视觉模型判断下一步。

零依赖：用标准库 urllib 直连 OpenAI 兼容的 chat/completions 接口（DeepSeek/Qwen/
GPT-4o 等），截图以 base64 data URL 传入 vision 模型。未配置 api_key 时
`available` 为 False，`decide` 直接返回 None（优雅降级到策略链下一层）。

配置（autoshot.yaml 或环境变量）：
  llm:
    base_url: https://api.openai.com/v1   # 或 AUTOSHOT_LLM_BASE_URL
    api_key: ""                           # 或 AUTOSHOT_LLM_API_KEY
    model: gpt-4o                         # 需 vision，或 AUTOSHOT_LLM_MODEL
    max_calls_per_key: 8                  # 每个 key 的 LLM 调用上限（token 预算）
    timeout: 30
    max_tokens: 300
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.request
from typing import Iterable, List, Optional

from .explorer import Action, Snapshot, _find_clickable
from .layout import normalize_text


def _extract_json(text: str) -> dict:
    """从模型输出里提取第一个 JSON 对象（容忍 markdown fence / 前后缀噪声）。"""
    if not text:
        return {}
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


class LLMClient:
    """OpenAI 兼容 chat/completions 的最小客户端（vision）。"""

    def __init__(self, llm_cfg: dict):
        self.base_url = (llm_cfg.get("base_url") or os.environ.get("AUTOSHOT_LLM_BASE_URL")
                         or "https://api.openai.com/v1").rstrip("/")
        self.api_key = llm_cfg.get("api_key") or os.environ.get("AUTOSHOT_LLM_API_KEY") or ""
        self.model = (llm_cfg.get("model") or os.environ.get("AUTOSHOT_LLM_MODEL")
                      or "gpt-4o")
        self.timeout = float(llm_cfg.get("timeout", 30))
        self.max_tokens = int(llm_cfg.get("max_tokens", 300))

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def decide_action(self, prompt: str, shot_bytes: bytes) -> dict:
        b64 = base64.b64encode(shot_bytes).decode("ascii")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content":
                    "你是 HarmonyOS 手机应用的 UI 自动化决策助手。给你当前屏幕截图、"
                    "控件树（含编号）、要寻找的目标文案和已尝试的历史。请判断下一步动作，"
                    "只返回一个 JSON 对象，不要任何解释。"},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ]},
            ],
            "max_tokens": self.max_tokens,
            "temperature": 0,
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        return _extract_json(content)


_SYSTEM_SCHEMA = (
    "允许返回的 JSON（action 取值）：\n"
    '  {"action":"click","node_id":<编号>}          点击编号对应节点\n'
    '  {"action":"click","text":"<按钮文案>"}       按文案点击（找不到编号时）\n'
    '  {"action":"scroll","direction":"up"|"down"}  滚动寻找（up=看下方内容）\n'
    '  {"action":"back"}                            返回上一页\n'
    '  {"action":"none"}                            确实无法继续\n'
)


class LLMPolicy:
    """启发式穷尽后的视觉决策。作为策略链第二层，返回 None 即放弃本层。"""

    def __init__(self, cfg, driver, page_adj: Optional[dict] = None, index=None):
        self.cfg = cfg
        self.driver = driver
        self.llm_cfg = getattr(cfg, "llm", None) or {}
        self.client = LLMClient(self.llm_cfg)
        self._calls = 0
        self._max_calls = int(self.llm_cfg.get("max_calls_per_key", 8))
        self._history: List[str] = []

    @staticmethod
    def _describe_nodes(snap: Snapshot) -> str:
        lines = []
        for i, n in enumerate(snap.nodes):
            t = normalize_text(n.text) or "(无文本)"
            flags = []
            if n.clickable:
                flags.append("可点")
            if n.scrollable:
                flags.append("可滚")
            lines.append(f"[{i}] {t}  center={n.center()} {' '.join(flags)}")
        return "\n".join(lines) or "（控件树为空）"

    def _to_action(self, result: dict, snap: Snapshot) -> Optional[Action]:
        kind = str(result.get("action", "")).strip().lower()
        if kind == "click":
            nid = result.get("node_id", result.get("node_index"))
            if isinstance(nid, int) and 0 <= nid < len(snap.nodes):
                return Action("click", node=snap.nodes[nid], reason=f"llm:{nid}")
            text = result.get("text")
            if text:
                n = _find_clickable(snap, str(text))
                if n:
                    return Action("click", node=n, reason=f"llm:{text}")
            return None
        if kind == "scroll":
            d = str(result.get("direction", "up")).strip().lower()
            return Action("scroll", direction=d if d in ("up", "down") else "up",
                          reason="llm:scroll")
        if kind == "back":
            return Action("back", reason="llm:back")
        return None

    def decide(self, snap: Snapshot, candidates: Iterable[str]) -> Optional[Action]:
        if not self.client.available:
            return None
        if self._calls >= self._max_calls:
            return None
        self._calls += 1
        try:
            shot = self.driver.screenshot()
        except Exception:
            return None
        cands = "、".join(c for c in candidates if c)
        prompt = (
            f"目标文案：{cands}\n\n"
            f"当前屏幕控件树：\n{self._describe_nodes(snap)}\n\n"
            f"已尝试的历史（避免重复）：\n"
            + ("\n".join(self._history[-6:]) or "（无）")
            + f"\n\n{_SYSTEM_SCHEMA}"
        )
        try:
            result = self.client.decide_action(prompt, shot)
        except Exception:
            return None
        action = self._to_action(result, snap)
        if action is not None:
            self._history.append(action.signature())
        return action
