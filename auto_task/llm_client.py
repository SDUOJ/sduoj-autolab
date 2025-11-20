"""Utilities for calling structured LLMs with optional multimodal inputs."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Type, TypeVar

import requests
from pydantic import BaseModel, ValidationError
from PIL import Image

from const import (
    LLM_DEEPSEEK_API_KEY,
    LLM_DEEPSEEK_BASE_URL,
    LLM_DEEPSEEK_MODEL,
    LLM_DOUBAO_API_KEY,
    LLM_DOUBAO_BASE_URL,
    LLM_DOUBAO_MODEL,
    LLM_QWEN_API_KEY,
    LLM_QWEN_BASE_URL,
    LLM_QWEN_MODEL,
)
from sduojApi import downloadFile

T = TypeVar("T", bound=BaseModel)
SCHEMA_PATTERN = re.compile(r"<schema>((?:(?!<schema>).)*?)</schema>", re.DOTALL | re.IGNORECASE)


@dataclass
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    is_multimodal: bool = False
    default_params: Dict[str, Any] = field(default_factory=dict)


class AutoTaskLLMClient:
    def __init__(self, config: LLMConfig):
        self.config = config

    def chat(self, messages: List[Dict[str, Any]], extra_params: Optional[Dict[str, Any]] = None) -> str:
        payload = {
            "model": self.config.model,
            "messages": messages,
        }
        payload.update(self.config.default_params)
        if extra_params:
            payload.update(extra_params)
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        resp = requests.post(
            self.config.base_url,
            json=payload,
            headers=headers,
            timeout=None,
        )
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            err_text = resp.text[:2000]
            raise RuntimeError(f"HTTP {resp.status_code}: {err_text}") from exc
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:  # pragma: no cover - defensive
            raise RuntimeError("LLM 返回内容缺失") from exc

    async def achat(self, messages: List[Dict[str, Any]], extra_params: Optional[Dict[str, Any]] = None) -> str:
        """Asynchronously chat with the LLM using a thread pool."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.chat, messages, extra_params)


def _is_config_ready(config: LLMConfig) -> bool:
    return bool(config.base_url and config.api_key and config.model)


QWEN_CONFIG = LLMConfig(
    base_url=LLM_QWEN_BASE_URL,
    api_key=LLM_QWEN_API_KEY,
    model=LLM_QWEN_MODEL,
    is_multimodal=True,
    default_params={
        "max_tokens": 8192,
        "temperature": 1.0,
        "top_p": 0.95,
        "top_k": 20,
        "repetition_penalty": 1.0,
        "presence_penalty": 0.0,
    },
)

DOUBAO_CONFIG = LLMConfig(
    base_url=LLM_DOUBAO_BASE_URL,
    api_key=LLM_DOUBAO_API_KEY,
    model=LLM_DOUBAO_MODEL,
    is_multimodal=True,
    default_params={
        "max_completion_tokens": 8192,
        "reasoning_effort": "medium",
    },
)

DEEPSEEK_CONFIG = LLMConfig(
    base_url=LLM_DEEPSEEK_BASE_URL,
    api_key=LLM_DEEPSEEK_API_KEY,
    model=LLM_DEEPSEEK_MODEL,
)


async def call_structured_llm(
        messages: Sequence[Dict[str, str]],
        schema_model: Type[T],
        image_file_ids: Optional[Sequence[str]] = None,
        max_retries: int = 3) -> T:
    """Call the appropriate LLM and enforce JSON-structured output."""

    has_images = bool(image_file_ids)
    configs: List[LLMConfig] = []
    if has_images:
        configs = [cfg for cfg in (QWEN_CONFIG, DOUBAO_CONFIG) if _is_config_ready(cfg) and cfg.is_multimodal]
        if not configs:
            raise ValueError("未找到可用的多模态 LLM 配置")
        image_contents = await _load_image_contents(image_file_ids or [])
    else:
        configs = [DEEPSEEK_CONFIG]
        image_contents = []

    base_messages = _build_conversation(messages, schema_model)
    errors: List[str] = []
    for config in configs:
        conversation = list(base_messages)
        client = AutoTaskLLMClient(config)
        last_error = ""
        for _ in range(max_retries):
            prepared_messages = _prepare_messages(conversation, image_contents, config.is_multimodal)
            try:
                response_text = await client.achat(prepared_messages)
            except Exception as exc:  # pragma: no cover - defensive fallback
                last_error = f"请求失败: {exc}"
                break
            conversation.append({"role": "assistant", "content": response_text})
            try:
                return _extract_and_validate(response_text, schema_model)
            except ValueError as exc:
                last_error = str(exc)
                correction = {
                    "role": "user",
                    "content": (
                        "解析你的输出时发生错误，错误信息如下："
                        f"{last_error}。\n"
                        "请重新生成回答，并严格遵守以下规则：\n"
                        "1. 必须使用 <schema> 和 </schema> 标签包裹 JSON 内容。\n"
                        "2. 标签内必须是纯文本的 JSON，不要使用 Markdown 格式。\n"
                        "3. 严格检查 JSON 语法，确保所有字符串中的特殊字符（特别是双引号 \" 和反斜杠 \\）都已正确转义。"
                    ),
                }
                conversation.append(correction)
        errors.append(f"{config.model}: {last_error or '未获得有效输出'}")

    raise ValueError(f"所有 LLM 尝试均失败，详情：{' | '.join(errors)}")


def _build_conversation(messages: Sequence[Dict[str, str]], schema_model: Type[T]) -> List[Dict[str, str]]:
    schema_json = schema_model.schema_json(indent=2, ensure_ascii=False)
    format_instruction = (
        "你是一个能够进行严谨推理并严格遵循结构化输出的助手。"
        "请严格遵循以下要求：\n"
        "1. 最终输出必须包含在 <schema> 和 </schema> 标签之间。\n"
        "2. 标签内部必须是标准的、合法的 JSON 格式字符串，不要使用 Markdown 代码块（如 ```json）。\n"
        "3. JSON 内容必须符合以下 Schema 定义：\n"
        f"{schema_json}\n"
        "4. 请特别注意 JSON 字符串的转义规则，确保所有特殊字符（如引号、换行符等）都被正确转义，以便可以被标准 JSON 解析器解析。"
    )
    conversation = [{"role": "system", "content": format_instruction}]
    for msg in messages:
        conversation.append({"role": msg["role"], "content": msg["content"]})
    return conversation


def _prepare_messages(
        messages: Sequence[Dict[str, str]],
        image_contents: Sequence[Dict[str, Any]],
        is_multimodal: bool) -> List[Dict[str, Any]]:
    prepared: List[Dict[str, Any]] = []
    image_attached = False
    for msg in messages:
        entry: Dict[str, Any] = {"role": msg["role"]}
        content = msg["content"]
        if is_multimodal and not image_attached and image_contents and msg["role"] == "user":
            entry["content"] = [
                {"type": "text", "text": content},
                *image_contents,
            ]
            image_attached = True
        else:
            entry["content"] = content
        prepared.append(entry)
    if image_contents and not image_attached:
        raise ValueError("未找到可附加图片的用户消息")
    return prepared


def _extract_and_validate(response_text: str, schema_model: Type[T]) -> T:
    matches = SCHEMA_PATTERN.findall(response_text)
    if not matches:
        print("LLM 输出中未找到 <schema> 标签")
        raise ValueError("未找到 <schema>...</schema> 标签包裹的内容")
    raw_json = matches[-1].strip()
    try:
        return schema_model.parse_raw(raw_json)
    except ValidationError as exc:
        raise ValueError(f"JSON 结构不符合 schema: {exc}") from exc


class _ImageDescription(BaseModel):
    text: str


async def describe_image_to_text(file_id: str) -> str:
    """Convert a single image (file_id) to a textual description."""

    prompt = (
        "### 角色定义\n"
        "你是一个数据结构与算法课程的专业助教，同时具备高精度的OCR能力。你的任务是将学生手写的答题图片（可能包含代码、数学推导、复杂数据结构示意图）转换为机器可读的结构化文本，供自动评分系统使用。\n\n"
        
        "### 核心原则\n"
        "1. **所见即所得**：如实记录内容，保留错误（不要修正学生的逻辑错误）。\n"
        "2. **语义显性化**：将图形中的视觉标记（颜色、形状、箭头）转化为明确的文本描述。\n"
        "3. **不解释，不拓展**：不要解释图中的内容，不要进行延伸拓展，客观的进行记录与描述。\n\n"
        
        "### 细分处理规则\n"
        
        "#### 1. 基础文本与代码\n"
        "- **文字**：完整转录，保留换行。\n"
        "- **数学公式**：使用 LaTeX 格式（如 $O(n^2)$）。\n"
        "- **代码**：保留代码缩进，代码中的高亮颜色无需描述。\n\n"
        
        "#### 2. 线性结构（数组、链表、栈、队列）\n"
        "- **内容表示**：使用方括号或箭头表示序列，例如 `[1, 2, 3, 4]` 或 `Head -> Node A -> Node B`。\n"
        "- **指针与引用**：\n"
        "  - 若有外部箭头指向某个元素，请描述为：`(指针 p 指向元素 2)`。\n"
        "  - 若是指针断裂或重连（如链表删除操作），请描述：`(原连接 A->B 被打叉，新增虚线连接 A->C)`。\n"
        "- **索引标记**：如果元素旁边标有下标（i, j, top, rear），请明确关联，例如 `元素 5 (下方标记: i)`。\n\n"
        
        "#### 3. 树形与图结构（二叉树、堆、图）\n"
        "- **结构描述**：优先使用 Mermaid 语法描述拓扑结构。\n"
        "- **若无法生成 Mermaid，使用缩进列表**，并必须包含节点属性。\n"
        "- **节点属性**：\n"
        "  - 若节点旁有数字（如平衡因子、深度），记录为 `节点 A {{属性: 平衡因子=1}}`。\n"
        "  - 若连接线有权重或方向，明确记录，如 `A --(权值:5)--> B`。\n\n"
        
        "#### 4. 视觉状态与特殊标记\n"
        "图片中可能包含表示算法状态的视觉记号，请按以下格式提取：\n"
        "- **颜色/阴影**：若某部分被涂色或高亮（不包括代码高亮），描述其语义。例如 `节点 C (状态: 红色/涂黑)` 或 `数组索引 0-3 (状态: 灰色阴影/已排序区域)`。\n"
        "- **形状标记**：例如 `节点 D 被双圈包裹 (可能表示终态)` 或 `元素 5 被划掉 (Strikethrough)`。\n"
        "- **辅助符号**：如有对勾、叉号、问号，请明确其位置和关联对象。\n\n"
    )

    result = await call_structured_llm(
        messages=[{"role": "user", "content": prompt}],
        schema_model=_ImageDescription,
        image_file_ids=[file_id],
    )
    return result.text


async def _load_image_contents(file_ids: Sequence[str]) -> List[Dict[str, Any]]:
    tasks = [downloadFile(file_id) for file_id in file_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    image_contents: List[Dict[str, Any]] = []
    for idx, result in enumerate(results):
        if isinstance(result, Exception):
            raise RuntimeError(f"下载文件 {file_ids[idx]} 失败: {result}")
        status, content, headers = result
        if status != 200:
            raise RuntimeError(f"下载文件 {file_ids[idx]} 失败，状态码 {status}")
        png_bytes = _coerce_image_to_png(content)
        mime = "image/png"
        encoded = base64.b64encode(png_bytes).decode("ascii")
        image_contents.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime};base64,{encoded}",
            }
        })
    return image_contents


def _coerce_image_to_png(content: bytes) -> bytes:
    try:
        image = Image.open(io.BytesIO(content))
    except Exception as exc:  # pragma: no cover - defensive
        raise RuntimeError("无法解析图片内容") from exc
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    max_bytes = 1_048_576  # 1 MB upper limit

    def _encode_png(img: Image.Image) -> bytes:
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    png_bytes = _encode_png(image)
    if len(png_bytes) <= max_bytes:
        return png_bytes

    width, height = image.size
    # Progressively downscale until the payload fits under 1 MB
    while len(png_bytes) > max_bytes and min(width, height) > 64:
        width = max(64, int(width * 0.8))
        height = max(64, int(height * 0.8))
        image = image.resize((width, height), Image.LANCZOS)
        png_bytes = _encode_png(image)

    # Final attempt: drop alpha channel if still large
    if len(png_bytes) > max_bytes and image.mode == "RGBA":
        image = image.convert("RGB")
        png_bytes = _encode_png(image)

    return png_bytes


__all__ = [
    "LLMConfig",
    "AutoTaskLLMClient",
    "call_structured_llm",
    "describe_image_to_text",
]
