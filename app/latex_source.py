"""Source-preserving import of an author's local LaTeX working project.

Only Introduction and Method bodies enter the editable block store. The rest
of the author's document is an immutable template, never interpreted or run.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re


def contained_path(root, value, *, directory=False):
    root = Path(root).resolve()
    if not isinstance(value, str) or not value.strip():
        raise ValueError("需要工作区内的 LaTeX 路径")
    path = Path(value)
    path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to(root):
        raise ValueError("LaTeX 来源和素材须先复制到论文工作区内")
    if directory:
        if not path.is_dir():
            raise ValueError("素材目录不存在")
    elif not path.is_file() or path.suffix.lower() != ".tex":
        raise ValueError("LaTeX 来源须为已有 .tex 文件")
    return path


def _mask_comments(text):
    # Preserve offsets while excluding commented commands from the parser.
    result = list(text)
    for match in re.finditer(r"(?m)^.*$", text):
        line = match.group()
        for index, char in enumerate(line):
            if char != "%":
                continue
            backslashes = 0
            cursor = index - 1
            while cursor >= 0 and line[cursor] == "\\":
                backslashes += 1
                cursor -= 1
            if backslashes % 2 == 0:
                for offset in range(match.start() + index, match.end()):
                    result[offset] = " "
                break
    return "".join(result)


def _headings(text, command):
    masked = _mask_comments(text)
    pattern = r"(?m)^[ \t]*\\" + command + r"\*?\s*\{"
    result = []
    for match in re.finditer(pattern, masked):
        opening = match.end() - 1
        depth, cursor = 1, opening + 1
        while cursor < len(masked) and depth:
            if masked[cursor] == "\\":
                cursor += 2
                continue
            if masked[cursor] == "{":
                depth += 1
            elif masked[cursor] == "}":
                depth -= 1
            cursor += 1
        if depth:
            raise ValueError("LaTeX 章节标题括号未闭合")
        # Heading labels stay in the original template, not in editable prose.
        label = re.match(r"\s*\\label\s*\{[^{}]+\}", masked[cursor:])
        end = cursor + label.end() if label else cursor
        result.append(dict(start=match.start(), end=end,
                           title=text[opening + 1:cursor - 1]))
    return result


def parse_source(text):
    """Return editable section descriptions and their exact source ranges."""
    if not isinstance(text, str) or len(text) > 5_000_000:
        raise ValueError("LaTeX 源码为空或过长")
    if not re.search(r"\\begin\s*\{document\}", text):
        raise ValueError("需要包含 document 环境的完整 LaTeX 源码")
    headings = _headings(text, "section")
    intro = next((i for i, h in enumerate(headings)
                  if h["title"].strip().lower() == "introduction"), None)
    method = next((i for i, h in enumerate(headings)
                   if h["title"].strip().lower() in {"method", "methods", "methodology"}), None)
    if intro is None or method is None or intro >= method:
        raise ValueError("未找到依次排列的 Introduction 与 Method 章节")
    if intro + 1 != method:
        raise ValueError("当前导入要求 Introduction 后紧接 Method；其他结构请先建立明确槽位")
    method_end = headings[method + 1]["start"] if method + 1 < len(headings) else text.rfind(r"\end{document}")
    if method_end < headings[method]["end"]:
        raise ValueError("Method 章节范围无效")
    method_body = text[headings[method]["end"]:method_end]
    children = _headings(method_body, "subsection")
    if len(children) != 5:
        raise ValueError("当前修订稿的 Method 应包含五个小节")
    slots = []

    def add(sid, title, role, level, parent, start, end):
        slots.append(dict(id=sid, title=title, role_id=role, level=level,
                          parent_id=parent, start=start, end=end,
                          source_type="original_latex_revision",
                          source_tex=text[start:end]))

    add("introduction", headings[intro]["title"], "introduction", 1, None,
        headings[intro]["end"], headings[method]["start"])
    offset = headings[method]["end"]
    add("method", headings[method]["title"], "method", 1, None,
        offset, offset + children[0]["start"])
    for index, child in enumerate(children):
        end = offset + children[index + 1]["start"] if index + 1 < len(children) else method_end
        add("method-3-" + str(index + 1), child["title"], "method", 2, "method",
            offset + child["end"], end)
    return slots


def build_binding(text, slots, document, metadata):
    by_id = {s["id"]: s for s in document["sections"]}
    ranges = []
    for slot in slots:
        body = slot["source_tex"]
        cursor, blocks = 0, []
        for block in by_id[slot["id"]]["blocks"]:
            start = body.find(block["tex"], cursor)
            if start < 0:
                raise ValueError("无法将编辑块映射回原 LaTeX 源码")
            end = start + len(block["tex"])
            blocks.append(dict(id=block["id"], start=start, end=end,
                               tex=block["tex"]))
            cursor = end
        ranges.append(dict(id=slot["id"], start=slot["start"], end=slot["end"],
                           body=body, blocks=blocks))
    return dict(format="original-latex-slots-v1", template=text,
                template_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                slots=ranges, **metadata)


def load_binding(folder):
    path = Path(folder) / "source_binding.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != "original-latex-slots-v1":
        raise ValueError("原版 LaTeX 槽位格式无法识别")
    if hashlib.sha256(data["template"].encode("utf-8")).hexdigest() != data.get("template_sha256"):
        raise ValueError("原版 LaTeX 模板指纹不匹配")
    return data


def render_source(binding, document, rendered=None, tracked=False):
    sections = {s["id"]: s for s in document["sections"]}
    expected = {s["id"] for s in binding["slots"]}
    if set(sections) != expected:
        raise ValueError("章节结构已变化，须先为原版 LaTeX 建立对应槽位")
    replacements = []
    for slot in binding["slots"]:
        current = sections[slot["id"]]["blocks"]
        originals = slot["blocks"]
        if [b["id"] for b in current] == [b["id"] for b in originals]:
            body = slot["body"]
            for original, block in reversed(list(zip(originals, current))):
                value = (rendered or {}).get(block["id"], block["tex"])
                body = body[:original["start"]] + value + body[original["end"]:]
        else:
            body = "\n\n" + "\n\n".join((rendered or {}).get(b["id"], b["tex"]) for b in current) + "\n\n"
        replacements.append((slot["start"], slot["end"], body))
    result = binding["template"]
    for start, end, body in reversed(replacements):
        result = result[:start] + body + result[end:]
    if tracked and rendered and not re.search(r"\\usepackage(?:\[[^\]]*\])?\s*\{[^}]*\bxcolor\b", result):
        result = result.replace(r"\begin{document}", "\\usepackage{xcolor}\n\\begin{document}", 1)
    return result


def public_metadata(binding):
    if not binding:
        return {}
    return dict(source_type="original_latex_revision",
                source_note="以作者原版 LaTeX 工程为底稿的可编辑修订工作稿；当前开放 Introduction 与 Method，模板、摘要和实验等其余源码保留。",
                source_reference=binding.get("original_path") or binding.get("path"),
                source_binding={key: binding.get(key) for key in
                                ("path", "original_path", "assets_path", "source_sha256", "original_sha256", "imported_at")},
                preserved_sections=["preamble", "abstract", "Experiments and Results", "Conclusion", "bibliography"])
