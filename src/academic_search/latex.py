"""LaTeX 转换前的有限预处理；只读源码包内文件，不执行 TeX。"""

from __future__ import annotations

import re
from pathlib import Path


def commands(text):
    """遍历有效控制词，跳过注释、转义字符和逐字环境。"""
    pattern = re.compile(r"%[^\n]*|\\(?:[A-Za-z@]+\*?|[^\n])")
    position = 0
    while match := pattern.search(text, position):
        position = match.end()
        token = match[0]
        if token.startswith("%"):
            continue
        name = token[1:]
        if name.rstrip("*") == "verb":
            if position < len(text):
                end = text.find(text[position], position + 1)
                position = end + 1 if end >= 0 else len(text)
            continue
        if name == "begin":
            arg = argument(text, position)
            if arg and arg[0] in {"verbatim", "verbatim*", "Verbatim", "lstlisting", "minted"}:
                end = text.find(r"\end{" + arg[0] + "}", arg[1])
                position = end + len(arg[0]) + 6 if end >= 0 else len(text)
                continue
        yield match.start(), match.end(), name


def argument(text, position, opener="{", closer="}"):
    """取平衡参数；跳过注释中的括号，保留嵌套与转义。"""
    while position < len(text):
        if text[position].isspace():
            position += 1
        elif text[position] == "%":
            end = text.find("\n", position)
            position = end if end >= 0 else len(text)
        else:
            break
    if position >= len(text) or text[position] != opener:
        return None
    start, depth = position + 1, 1
    position += 1
    while position < len(text):
        char = text[position]
        if char == "\\":
            position += 2
            continue
        if char == "%":
            end = text.find("\n", position)
            position = end if end >= 0 else len(text)
            continue
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start:position], position + 1
        position += 1
    return None


def replace(text, changes):
    for start, end, value in sorted(changes, reverse=True):
        text = text[:start] + value + text[end:]
    return text


def expand_inputs(root_tex: str, source_dir: str) -> str:
    """展开静态 input/include；循环、越界和膨胀超限明确失败。"""
    boundary = Path(source_dir).resolve()
    cwd = Path(root_tex).resolve().parent
    size = 0

    def expand(file, chain):
        nonlocal size
        file = file.resolve()
        if not file.is_relative_to(boundary):
            raise ValueError("LaTeX input 超出源码目录")
        if file in chain or len(chain) >= 40:
            raise ValueError("LaTeX input 循环或嵌套超过 40 层：" + file.name)
        text = file.read_text(encoding="utf-8", errors="replace")
        size += len(text)
        if size > 16 * 1024 * 1024:
            raise ValueError("LaTeX 展开超过 16 MiB")
        changes = []
        for start, end, name in commands(text):
            if name not in {"input", "include"}:
                continue
            arg = argument(text, end)
            if arg is None and name == "input":
                match = re.match(r"\s+([^\s%{}]+)", text[end:])
                if match:
                    arg = match[1], end + match.end()
            if not arg or any(char in arg[0] for char in "\\#{}"):
                continue  # 动态宏参数交给 pandoc，不能猜文件名。
            relative = Path(arg[0].strip())
            if relative.suffix == "":
                relative = relative.with_suffix(".tex")
            candidates = [(cwd / relative).resolve(), (file.parent / relative).resolve()]
            if any(not item.is_relative_to(boundary) for item in candidates):
                raise ValueError("LaTeX input 超出源码目录：" + str(relative))
            included = next((item for item in candidates if item.is_file()), None)
            if included is None:
                raise ValueError("LaTeX input 文件不存在：" + str(relative))
            changes.append((start, arg[1], expand(included, chain + (file,))))
        return replace(text, changes)

    return expand(Path(root_tex), ())


def prepare(text: str) -> str:
    """将摘要宏移到正文，去掉仅负责表格排版的 cmidrule。"""
    changes, abstracts = [], []
    document_end = title_end = None
    for start, end, name in commands(text):
        if name == "begin":
            arg = argument(text, end)
            if arg and arg[0] == "document":
                document_end = arg[1]
        elif name == "maketitle" and document_end is not None and title_end is None:
            title_end = end
        elif name == "abstract":
            arg = argument(text, end)
            if arg:
                content = "\n\\section*{Abstract}\n" + arg[0] + "\n"
                if document_end is None:
                    abstracts.append(content)
                    content = ""
                changes.append((start, arg[1], content))
        elif name == "cmidrule":
            option = argument(text, end, "[", "]")
            if option:
                end = option[1]
            trim = argument(text, end, "(", ")")
            if trim:
                end = trim[1]
            span = argument(text, end)
            if span:
                changes.append((start, span[1], ""))
    if abstracts:
        if document_end is None:
            raise ValueError("摘要宏存在但没有 document 环境")
        position = title_end or document_end
        changes.append((position, position, "".join(abstracts)))
    return replace(text, changes)


def bibliographies(text: str, root_tex: str, source_dir: str) -> list[str]:
    """按源码声明顺序解析全部 bib；不存在或越界不静默换库。"""
    boundary, cwd = Path(source_dir).resolve(), Path(root_tex).resolve().parent
    found = []
    declared = False
    for _, end, name in commands(text):
        if name not in {"bibliography", "addbibresource"}:
            continue
        declared = True
        option = argument(text, end, "[", "]")
        arg = argument(text, option[1] if option else end)
        if arg is None:
            continue
        for name in arg[0].split(","):
            file = cwd / name.strip()
            if file.suffix.lower() != ".bib":
                file = file.with_suffix(".bib")
            file = file.resolve()
            if not file.is_relative_to(boundary) or not file.is_file():
                raise ValueError("声明的文献库不存在或超出源码目录：" + name)
            if str(file) not in found:
                found.append(str(file))
    if not declared:
        matches = sorted(cwd.glob("*.bib"))
        found = [str(matches[0])] if matches else []
    return found
