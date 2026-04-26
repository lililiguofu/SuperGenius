"""从飞书消息附件下载并提取文本（支持 .txt / .pdf / .docx）。

群聊中连续多文件时，拉取资源接口可能偶发失败或空包，这里带有限次重试；
失败时返回以 `[` 开头的说明串（简历正文请勿以方括号开头以免误判）。
"""

from __future__ import annotations

import io
import time
from typing import Any

from loguru import logger


def _extract_text(file_name: str, raw: bytes) -> str | None:
    if not raw:
        return None

    ext = file_name.lower().rsplit(".", 1)[-1] if "." in file_name else ""

    if ext == "txt":
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    if ext == "pdf":
        try:
            import pypdf  # type: ignore[import-untyped]

            reader = pypdf.PdfReader(io.BytesIO(raw))
            pages = [page.extract_text() or "" for page in reader.pages]
            return "\n".join(pages).strip()
        except ImportError:
            logger.warning("pypdf 未安装，PDF 无法解析；请 uv add pypdf")
            return f"[无法解析 PDF：{file_name}，请安装 pypdf]"
        except Exception as exc:
            return f"[PDF 解析失败：{exc}]"

    if ext in ("docx", "doc"):
        try:
            import docx  # type: ignore[import-untyped]

            document = docx.Document(io.BytesIO(raw))
            return "\n".join(p.text for p in document.paragraphs).strip()
        except ImportError:
            logger.warning("python-docx 未安装，DOCX 无法解析；请 uv add python-docx")
            return f"[无法解析 DOCX：{file_name}，请安装 python-docx]"
        except Exception as exc:
            return f"[DOCX 解析失败：{exc}]"

    return raw.decode("utf-8", errors="replace")


def _download_bytes_once(
    lark_client: Any,
    message_id: str,
    file_key: str,
) -> tuple[bool, bytes, str]:
    from lark_oapi.api.im.v1 import GetMessageResourceRequest  # type: ignore[import-untyped]

    request = (
        GetMessageResourceRequest.builder()
        .message_id(message_id)
        .file_key(file_key)
        .type("file")
        .build()
    )
    resp = lark_client.im.v1.message_resource.get(request)
    if not resp.success():
        c = getattr(resp, "code", None)
        m = (getattr(resp, "msg", None) or "").strip() or "unknown"
        return False, b"", f"飞书 API code={c}，msg={m}"
    if resp.file is None:
        st = getattr(resp.raw, "status_code", None) if resp.raw is not None else None
        return False, b"", f"无文件体 HTTP={st}"
    try:
        raw: bytes = resp.file.read() or b""
    except Exception as exc:  # noqa: BLE001
        return False, b"", f"读文件流失败: {exc!s}"
    if not raw:
        return False, b"", "文件大小 0 字节（多为接口抖动，可单独重发该文件试一次）"
    return True, raw, ""


def download_and_parse(
    lark_client: Any,
    message_id: str,
    file_key: str,
    file_name: str,
) -> str:
    """返回简历正文，或**以方括号 [ 开头**的简短错误信息（给机器人展示）。"""
    if not (message_id and file_key):
        return "[file] 消息缺少 file_key，无法从飞书拉取该附件"

    last_err = ""
    raw: bytes = b""
    for attempt in range(3):
        ok, data, err = _download_bytes_once(lark_client, message_id, file_key)
        if ok and data:
            raw = data
            break
        last_err = err or "未知错误"
        logger.warning(f"[file_ingest] 第 {attempt + 1}/3 次拉取失败 {file_name!r}: {last_err}")
        if attempt < 2:
            time.sleep(0.35 * (2**attempt))
    else:
        return f"[file] 下载失败：{last_err}"

    text = _extract_text(file_name, raw)
    if text is not None and text.startswith("["):
        return text
    if text is None or not str(text).strip():
        return "[file] 解码后无文本（请用 UTF-8/GBK 的 .txt 或重发）"
    logger.info(f"[file_ingest] {file_name} 解析完成，{len(text)} 字")
    return text
