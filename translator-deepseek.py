"""
Markdown 英译中，使用 DeepSeek API。
模型 deepseek-chat（DeepSeek-V3.2）：上下文 128k，单次输出最大 8k tokens。
词表（glossary）放在每条请求的 system 消息开头且内容固定，以利用 DeepSeek 的 Context Caching 降低 token 消耗。
"""
import argparse
import os
import re
import time
from pathlib import Path
from typing import List, Tuple

from openai import OpenAI


MODEL_NAME = "deepseek-chat"
BASE_URL = "https://api.deepseek.com"
SOURCE_EXT = ".md"
DEFAULT_OUTPUT_DIR_NAME = "translated"
# 128k 上下文，单块不必过大；单次输出上限 8k tokens，约 4000 字符输入较安全
MAX_CHARS_PER_CHUNK = 4000
MAX_OUTPUT_TOKENS = 8192
REQUEST_INTERVAL_SECONDS = 0.2
RETRY_TIMES = 3

_DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
if not _DEEPSEEK_API_KEY:
    raise RuntimeError(
        "DEEPSEEK_API_KEY environment variable is not set. "
        "Set it before running, e.g.: export DEEPSEEK_API_KEY='your-key'"
    )
client = OpenAI(api_key=_DEEPSEEK_API_KEY, base_url=BASE_URL)


def read_text(file_path: Path) -> str:
    return file_path.read_text(encoding="utf-8")


def write_text(file_path: Path, content: str) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")


def split_front_matter(text: str) -> Tuple[str, str]:
    if not text.startswith("---\n"):
        return "", text

    match = re.match(r"^---\n.*?\n---\n?", text, flags=re.DOTALL)
    if not match:
        return "", text

    front_matter = match.group(0)
    body = text[len(front_matter):]
    return front_matter, body


def protect_code_blocks(text: str) -> Tuple[str, List[str]]:
    code_blocks = []

    def replacer(match: re.Match) -> str:
        code_blocks.append(match.group(0))
        return f"\n\n__CODE_BLOCK_{len(code_blocks) - 1}__\n\n"

    protected = re.sub(r"```[\s\S]*?```", replacer, text)
    return protected, code_blocks


def protect_inline_code(text: str) -> Tuple[str, List[str]]:
    """将行内代码 `...` 替换为占位符，避免被翻译。"""
    inline_codes = []

    def replacer(match: re.Match) -> str:
        inline_codes.append(match.group(0))
        return f"__INLINE_CODE_{len(inline_codes) - 1}__"

    protected = re.sub(r"`[^`\n]*?`", replacer, text)
    return protected, inline_codes


def restore_inline_code(text: str, inline_codes: List[str]) -> str:
    for index, code in enumerate(inline_codes):
        text = text.replace(f"__INLINE_CODE_{index}__", code)
    return text


def restore_code_blocks(text: str, code_blocks: List[str]) -> str:
    for index, block in enumerate(code_blocks):
        text = text.replace(f"__CODE_BLOCK_{index}__", block)
    return text


def split_into_chunks(text: str, max_chars: int) -> List[str]:
    paragraphs = re.split(r"(\n\s*\n)", text)
    chunks = []
    current = ""

    for part in paragraphs:
        if len(current) + len(part) <= max_chars:
            current += part
            continue

        if current.strip():
            chunks.append(current)
            current = ""

        if len(part) <= max_chars:
            current = part
        else:
            lines = part.splitlines(keepends=True)
            buffer = ""
            for line in lines:
                if len(buffer) + len(line) <= max_chars:
                    buffer += line
                else:
                    if buffer:
                        chunks.append(buffer)
                    buffer = line
            if buffer:
                current = buffer

    if current.strip():
        chunks.append(current)

    return chunks


def is_translatable_chunk(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False

    if re.fullmatch(r"__CODE_BLOCK_\d+__", stripped):
        return False
    # 仅含行内代码占位符时无需调用 API
    if not re.sub(r"__INLINE_CODE_\d+__", "", stripped).strip():
        return False

    return True


def build_body_system_prompt(glossary_text: str = "") -> str:
    """构建正文翻译的 system 消息。同一 session 内复用同一字符串，便于 DeepSeek Context Caching 命中。"""
    s = (
        "You are a professional technical translator. Translate the following Markdown from English to Simplified Chinese.\n\n"
        "**Structure & format**\n"
        "1. Preserve Markdown structure, links, image paths, YAML keys, and syntax exactly.\n"
        "2. Do not translate fenced code blocks or inline code (single backticks).\n"
        "3. Leave any __INLINE_CODE_N__ placeholder (N is a number) exactly as-is.\n\n"
        "**Terminology (important)**\n"
        "4. Use standard, established Chinese technical terms only. Do NOT invent new compound words.\n"
        "5. When a term has no widely accepted translation or you are unsure, keep the English term (optionally add Chinese in parentheses).\n"
        "6. Keep product names, API names, framework names, and code identifiers in English when they are proper nouns or technical identifiers.\n"
    )
    if glossary_text:
        s += (
            "\n\n**Glossary — use these terms consistently (same English term → same Chinese):**\n"
            + glossary_text
            + "\n\n"
        )
    s += "Return only the translated Markdown text, no explanation."
    return s


def build_code_comments_system_prompt(glossary_text: str = "") -> str:
    """构建代码块注释翻译的 system 消息。复用同一字符串以利于 DeepSeek Context Caching。"""
    s = (
        "You are a technical translator. Translate ONLY the comments in the following code block from English to Simplified Chinese.\n"
        "Rules:\n"
        "1. Translate // line comments, # line comments, /* */ block comments, <!-- --> HTML comments, and similar.\n"
        "2. Do NOT change any code, string literals, identifiers, or syntax.\n"
        "3. Use standard Chinese technical terms; do not invent compound words.\n"
        "4. Return the complete code block with only comment text translated; keep formatting and structure identical.\n"
    )
    if glossary_text:
        s += "\n\n**Glossary (use consistently):**\n" + glossary_text + "\n\n"
    return s


def translate_chunk(chunk: str, system_prompt: str) -> str:
    """system_prompt 应为预构建的固定字符串，以便 DeepSeek 缓存前缀。"""
    for attempt in range(1, RETRY_TIMES + 1):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": chunk},
                ],
                temperature=0,
                max_tokens=MAX_OUTPUT_TOKENS,
            )
            return response.choices[0].message.content or chunk
        except Exception:
            if attempt == RETRY_TIMES:
                return chunk
            time.sleep(1.5 * attempt)

    return chunk


def translate_code_block_comments(block: str, system_prompt: str) -> str:
    """只翻译代码块中的注释。system_prompt 预构建并复用，便于 DeepSeek 缓存。"""
    for attempt in range(1, RETRY_TIMES + 1):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": block},
                ],
                temperature=0,
                max_tokens=MAX_OUTPUT_TOKENS,
            )
            result = response.choices[0].message.content
            if result and result.strip():
                return result
            return block
        except Exception:
            if attempt == RETRY_TIMES:
                return block
            time.sleep(1.5 * attempt)
    return block


def translate_markdown(
    text: str,
    *,
    body_system_prompt: str = "",
    code_comments_system_prompt: str = "",
    verbose: bool = True,
) -> str:
    """body_system_prompt 与 code_comments_system_prompt 在 session 内固定，便于 DeepSeek 缓存词表。"""
    front_matter, body = split_front_matter(text)
    protected_body, code_blocks = protect_code_blocks(body)
    protected_body, inline_codes = protect_inline_code(protected_body)
    chunks = split_into_chunks(protected_body, MAX_CHARS_PER_CHUNK)

    translatable_count = sum(1 for c in chunks if is_translatable_chunk(c))
    if verbose and translatable_count > 0:
        print(f"  Body: {translatable_count} chunk(s) to translate")

    translated_chunks = []
    done = 0
    for chunk in chunks:
        if is_translatable_chunk(chunk):
            done += 1
            if verbose:
                print(f"  Body chunk {done}/{translatable_count}", flush=True)
            translated = translate_chunk(chunk, system_prompt=body_system_prompt)
            translated_chunks.append(translated)
            time.sleep(REQUEST_INTERVAL_SECONDS)
        else:
            translated_chunks.append(chunk)

    translated_body = "".join(translated_chunks)
    translated_body = restore_inline_code(translated_body, inline_codes)

    if verbose and code_blocks:
        print(f"  Code blocks: {len(code_blocks)} to translate (comments only)", flush=True)
    code_blocks_with_translated_comments = []
    for i, block in enumerate(code_blocks):
        if verbose:
            print(f"  Code block {i + 1}/{len(code_blocks)}", flush=True)
        code_blocks_with_translated_comments.append(
            translate_code_block_comments(block, system_prompt=code_comments_system_prompt)
        )
        time.sleep(REQUEST_INTERVAL_SECONDS)
    translated_body = restore_code_blocks(translated_body, code_blocks_with_translated_comments)
    return front_matter + translated_body


def collect_markdown_files(root_dir: Path, output_dir: Path) -> List[Path]:
    files = []
    for file_path in root_dir.rglob(f"*{SOURCE_EXT}"):
        if output_dir in file_path.parents:
            continue
        files.append(file_path)
    return sorted(files)


def build_output_path(src_file: Path, root_dir: Path, output_dir: Path) -> Path:
    relative_path = src_file.relative_to(root_dir)
    return output_dir / relative_path


def files_needing_translation(
    md_files: List[Path], root_dir: Path, output_dir: Path
) -> List[Path]:
    """只保留在 out 目录中尚无对应翻译文件的源文件；out 中已存在的视为已翻译，跳过。"""
    return [f for f in md_files if not build_output_path(f, root_dir, output_dir).exists()]


def load_glossary(path: Path) -> str:
    """从 Markdown 词表文件中解析「英文 -> 中文」术语对，返回供 prompt 使用的文本。"""
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    glossary: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or "|" not in line[1:]:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            continue
        term, trans = parts[1], parts[2]
        if not term or not trans:
            continue
        if term.lower() in ("term", "component") or "---" in trans:
            continue
        if term not in glossary:
            glossary[term] = trans
    if not glossary:
        return ""
    return "\n".join(f"- {k}: {v}" for k, v in sorted(glossary.items()))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Translate Markdown files from English to Simplified Chinese (DeepSeek)."
    )
    parser.add_argument(
        "--folder",
        type=Path,
        required=True,
        help="Directory containing the Markdown files to translate.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output directory for translated files. Default: <folder>/translated",
    )
    parser.add_argument(
        "--glossary",
        type=Path,
        default=None,
        help="Path to glossary Markdown file (e.g. glossary.md). Optional.",
    )
    args = parser.parse_args()
    if args.out is None:
        args.out = args.folder / DEFAULT_OUTPUT_DIR_NAME
    return args


def main() -> None:
    args = parse_args()
    root_dir = args.folder.resolve()
    output_dir = args.out.resolve()

    if not root_dir.is_dir():
        raise RuntimeError(f"Folder does not exist or is not a directory: {root_dir}")

    all_md = collect_markdown_files(root_dir, output_dir)
    if not all_md:
        print("No markdown files found.")
        return

    md_files = files_needing_translation(all_md, root_dir, output_dir)
    skipped = len(all_md) - len(md_files)
    if skipped:
        print(f"Found {len(all_md)} markdown files, {skipped} already in output (skipped), {len(md_files)} to translate.")
    else:
        print(f"Found {len(md_files)} markdown files.")

    glossary_path = args.glossary or Path.cwd() / "glossary.md"
    glossary_text = load_glossary(glossary_path)
    body_system_prompt = build_body_system_prompt(glossary_text)
    code_comments_system_prompt = build_code_comments_system_prompt(glossary_text)
    if glossary_text:
        print(f"Loaded glossary from {glossary_path} ({len(glossary_text.splitlines())} terms, will use DeepSeek context cache).")
    else:
        print("No glossary used (missing or empty).")

    for index, src_file in enumerate(md_files, start=1):
        dst_file = build_output_path(src_file, root_dir, output_dir)
        if dst_file.exists():
            print(f"[{index}/{len(md_files)}] Skip (already in output): {src_file}")
            continue

        print(f"[{index}/{len(md_files)}] Translating: {src_file}", flush=True)
        try:
            original_text = read_text(src_file)
            translated_text = translate_markdown(
                original_text,
                body_system_prompt=body_system_prompt,
                code_comments_system_prompt=code_comments_system_prompt,
                verbose=True,
            )
            write_text(dst_file, translated_text)
            print(f"[{index}/{len(md_files)}] Done: {src_file}")
        except Exception as exc:
            print(f"[{index}/{len(md_files)}] Failed: {src_file} -> {exc}")


if __name__ == "__main__":
    main()
