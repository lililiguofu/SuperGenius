"""LLM 抽象封装（OpenAI 兼容协议）。"""

from supergenius.llm.client import LLMClient, render_prompt

__all__ = ["LLMClient", "render_prompt"]
