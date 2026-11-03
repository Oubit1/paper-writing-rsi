"""Conservative PDF text to exact source mapping; no fuzzy TeX reconstruction."""
import re
import unicodedata

from .manuscript import ManuscriptConflict, _math_spans


def _normal(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def _variants(text):
    return {_normal(text), _normal(re.sub(r"(?<=\w)-[ \t]*\r?\n\s*(?=\w)", "", text)),
            _normal(re.sub(r"-[ \t]*\r?\n\s*", "-", text))}


def _context_matches(visible, start, end, before, after):
    # A command may hide PDF citation/formatting syntax. Context stops at its
    # nearest barrier; the actual selected range is never allowed across one.
    left = visible[visible.rfind("\0", 0, start) + 1:start].strip()
    boundary = visible.find("\0", end)
    right = visible[end:boundary if boundary >= 0 else len(visible)].strip()
    left_ok = not before or not left or any(left.endswith(v) or v.endswith(left) for v in _variants(before))
    right_ok = not after or not right or any(right.startswith(v) or v.startswith(right) for v in _variants(after))
    return left_ok and right_ok


def _visible_map(tex):
    """Keep literal text runs; opaque commands/math are uncrossable barriers."""
    hidden = [False] * len(tex)
    for a, b, _ in _math_spans(tex):
        hidden[a:b] = [True] * (b - a)
    for match in re.finditer(r"(?<!\\)%[^\n]*|\\(?:[A-Za-z@]+\*?|[^A-Za-z\s])", tex):
        a, b = match.span()
        if tex[a] == "\\" and re.match(r"\\[A-Za-z@]+", match.group()):
            command = match.group().lstrip("\\").rstrip("*")
            if command not in {"textbf", "textit", "emph", "textrm", "textsf", "texttt", "textnormal"}:
                cursor = b
                while cursor < len(tex):
                    gap = re.match(r"\s*", tex[cursor:]).end()
                    begin = cursor + gap
                    if begin >= len(tex) or tex[begin] not in "[{":
                        break
                    opening, closing = tex[begin], "]" if tex[begin] == "[" else "}"
                    depth, end = 1, begin + 1
                    while end < len(tex) and depth:
                        if tex[end] == "\\":
                            end += 2
                            continue
                        depth += (tex[end] == opening) - (tex[end] == closing)
                        end += 1
                    if depth:
                        break
                    cursor = end
                    b = end
        hidden[a:b] = [True] * (b - a)
    chars, positions = [], []
    for index, char in enumerate(tex):
        if hidden[index] or char in "{}&~":
            chars.append("\0")
            positions.append((index, index + 1))
        elif char.isspace():
            if chars and chars[-1] == " ":
                positions[-1] = (positions[-1][0], index + 1)
            else:
                chars.append(" ")
                positions.append((index, index + 1))
        else:
            for normalized in unicodedata.normalize("NFKC", char):
                chars.append(normalized)
                positions.append((index, index + 1))
    return "".join(chars), positions


def map_selection(state, area, selection):
    if not isinstance(selection, dict):
        raise ValueError("选区必须包含文字及当前 PDF 版本。")
    if set(selection) - {"text", "original_tex", "before_context", "after_context", "page", "pdf_source_hash", "context", "source"}:
        raise ValueError("选区包含未知字段。")
    if selection.get("pdf_source_hash") and selection["pdf_source_hash"] != state["hash"]:
        raise ManuscriptConflict("所选 PDF 不是当前稿件，请先编译当前版本。", {"hash": state["hash"]})
    pdf = selection.get("source") == "pdf" or "text" in selection
    if pdf and selection.get("pdf_source_hash") != state["hash"]:
        raise ManuscriptConflict("PDF 选区缺少当前源码版本，未猜测修改位置。", {"hash": state["hash"]})
    value = selection.get("original_tex", selection.get("text"))
    if not isinstance(value, str) or not value.strip() or len(value) > 10000:
        raise ValueError("请选择不超过 10000 字符的局部文字。")
    tex = state["tex"][area["start"]:area["end"]]
    if "original_tex" in selection:
        before, after = selection.get("before_context", ""), selection.get("after_context", "")
        if not isinstance(before, str) or not isinstance(after, str) or max(len(before), len(after)) > 500:
            raise ValueError("精确选区的前后片段格式无效。")
        matches = [m.start() for m in re.finditer(re.escape(value), tex)
                   if (not before or tex[:m.start()].endswith(before)) and
                   (not after or tex[m.end():].startswith(after))]
        if len(matches) != 1:
            raise ValueError("选区原文未能唯一定位，请缩小或补充前后文。")
        a, b = matches[0], matches[0] + len(value)
    else:
        visible, positions = _visible_map(tex)
        before, after = selection.get("before_context", ""), selection.get("after_context", "")
        if not isinstance(before, str) or not isinstance(after, str) or max(len(before), len(after)) > 1000:
            raise ValueError("PDF 选区的前后可见文字格式无效。")
        variants = _variants(value)
        found = set()
        for needle in variants:
            cursor = 0
            while needle:
                pos = visible.find(needle, cursor)
                if pos < 0:
                    break
                end = pos + len(needle)
                if (pos == 0 or positions[pos - 1] != positions[pos]) and (
                        end == len(positions) or positions[end - 1] != positions[end]) and _context_matches(
                            visible, pos, end, before, after):
                    found.add((positions[pos][0], positions[end - 1][1]))
                cursor = pos + 1
        if len(found) != 1:
            raise ValueError("PDF 选区无法唯一且安全地对应源码；公式、引用或命令边界不能猜测，请改选连续正文。")
        a, b = found.pop()
    return {**area, "start": area["start"] + a, "end": area["start"] + b,
            "tex": tex[a:b], "selection_text": value, "selection_source": "pdf" if pdf else "source"}


def map_layout_selection(state, area, selection):
    """A layout selection identifies one float, not a caption text edit."""
    try:
        selected = map_selection(state, area, selection)
    except ManuscriptConflict:
        raise
    except ValueError:
        selected = None
    if not isinstance(selection, dict) or set(selection) - {"text", "original_tex", "before_context", "after_context", "page", "pdf_source_hash", "context", "source"}:
        raise ValueError("布局选区格式无效。")
    value = selection.get("original_tex", selection.get("text"))
    if not isinstance(value, str) or not value.strip() or len(value) > 30000:
        raise ValueError("布局选区文字无效或过长。")
    if ("text" in selection or selection.get("source") == "pdf") and selection.get("pdf_source_hash") != state["hash"]:
        raise ManuscriptConflict("布局选区的 PDF 已过期，请先编译当前版本。", {"hash": state["hash"]})
    text = state["tex"][area["start"]:area["end"]]
    candidates = []
    for match in re.finditer(r"\\begin\{(figure\*?|table\*?)\}.*?\\end\{\1\}", text, re.S):
        start, end = area["start"] + match.start(), area["start"] + match.end()
        if selected and start <= selected["start"] <= selected["end"] <= end:
            candidates.append((start, end))
            continue
        for caption in re.finditer(r"\\caption(?:\[[^\]]*\])?\s*\{", match.group()):
            begin, cursor, depth = caption.end(), caption.end(), 1
            while cursor < len(match.group()) and depth:
                char = match.group()[cursor]
                if char == "\\":
                    cursor += 2
                    continue
                depth += (char == "{") - (char == "}")
                cursor += 1
            if depth:
                continue
            visible, _ = _visible_map(match.group()[begin:cursor-1])
            if any(needle and needle in visible for needle in _variants(value)):
                candidates.append((start, end))
                break
    candidates = set(candidates)
    if len(candidates) == 1:
        start, end = candidates.pop()
        return {**area, "start": start, "end": end, "tex": state["tex"][start:end],
                "selection_text": value, "selection_source": "layout_object", "layout_object": True}
    if not candidates and selected:
        # A precisely selected standalone spacing/alignment command may be
        # handled by the layout validator, never expanded to unrelated text.
        return selected
    raise ValueError("布局选区不能唯一对应一个图表；请选唯一图注或明确图表，未猜测调整对象。")
