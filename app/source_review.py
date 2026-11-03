"""Author-controlled full-source reviews. Candidates never become current text."""
from __future__ import annotations

from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import urllib.parse

from .latex_source import contained_path, _headings, _mask_comments
from .manuscript import ManuscriptConflict, split_tex_blocks, _math_spans, _escaped
from .store import Store, digest, now, uid


ACTIVE = {"pending", "partially_accepted"}
TEXT_EXTENSIONS = {".tex", ".bib", ".cls", ".sty", ".txt"}
ASSET_EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".png", ".jpg", ".jpeg", ".eps"}
DISALLOWED = {
    "openin", "openout", "closein", "closeout", "read", "readline", "write",
    "immediate", "directlua", "latelua", "luaexec", "shellescape", "shellesc",
    "special", "scantokens", "catcode", "csname", "endcsname", "input",
    "include", "includeonly", "verbatiminput", "lstinputlisting", "inputminted",
}


def normalized(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def validate_source(tex):
    """Validate text only. No TeX engine or user macros are executed here."""
    if not isinstance(tex, str) or not tex.strip() or len(tex) > 5_000_000:
        raise ValueError("需要不超过 5000000 字符的完整 LaTeX 稿件")
    if any(ord(c) < 32 and c not in "\r\n\t" for c in tex) or "^^" in tex:
        raise ValueError("源码包含不支持的控制字符")
    code = _mask_comments(tex)
    commands = {m.lower().lstrip("@") for m in re.findall(r"\\([A-Za-z@]+)", code)}
    if commands & DISALLOWED:
        raise ValueError("源码包含文件读写或执行命令：" + ", ".join(sorted(commands & DISALLOWED)))
    if len(re.findall(r"\\begin\s*\{document\}", code)) != 1 or len(re.findall(r"\\end\s*\{document\}", code)) != 1:
        raise ValueError("完整源码须有且仅有一个 document 环境")
    depth = 0
    for match in re.finditer(r"\\.|[{}]", code, re.S):
        if match.group() == "{":
            depth += 1
        elif match.group() == "}":
            depth -= 1
            if depth < 0:
                raise ValueError("LaTeX 花括号未配对")
    if depth:
        raise ValueError("LaTeX 花括号未配对")
    stack = []
    for match in re.finditer(r"\\(begin|end)\s*\{([^{}]+)\}", code):
        action, environment = match.groups()
        if action == "begin":
            stack.append(environment)
        elif not stack or stack.pop() != environment:
            raise ValueError("LaTeX 环境未配对：" + environment)
    if stack:
        raise ValueError("LaTeX 环境未闭合：" + stack[-1])
    return tex


def source_outline(tex):
    result = []
    for level, command in ((1, "section"), (2, "subsection"), (3, "subsubsection")):
        for h in _headings(tex, command):
            label = re.search(r"\\label\s*\{([^{}]+)\}", tex[h["start"]:h["end"]])
            plain = re.sub(r"\\\w+\{([^{}]*)\}", r"\1", h["title"]).strip()
            key = label.group(1) if label else re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")
            result.append(dict(id=key or f"heading-{h['start']}", title=plain, level=level,
                               line=tex.count("\n", 0, h["start"]) + 1,
                               start=h["start"], heading_end=h["end"], end=len(tex)))
    result.sort(key=lambda h: h["start"])
    for i, heading in enumerate(result):
        heading["end"] = next((h["start"] for h in result[i + 1:] if h["level"] <= heading["level"]),
                              tex.rfind(r"\end{document}") if heading["level"] == 1 else len(tex))
    return result


def risks(tex):
    code = _mask_comments(tex)
    labels = re.findall(r"\\label\s*\{([^{}]+)\}", code)
    refs = set(re.findall(r"\\(?:ref|eqref|pageref)\s*\{([^{}]+)\}", code))
    result = []
    duplicates = sorted({x for x in labels if labels.count(x) > 1})
    if duplicates:
        result.append(dict(kind="duplicate_labels", message="分步采纳后请核对重复标签", items=duplicates))
    missing = sorted(refs - set(labels))
    if missing:
        result.append(dict(kind="unresolved_references", message="关联引用仍需同步核查", items=missing))
    return result


def _scope(tex, scope):
    if scope in {None, "", "document", "all"}:
        return dict(id="document", title="全文", start=0, end=len(tex), heading_end=0, level=0)
    alias = {"intro": "introduction", "method": "methodology", "methods": "methodology"}
    value = alias.get(scope.lower(), scope.lower())
    found = next((h for h in source_outline(tex)
                  if h["id"] == scope or h["title"].lower() == value), None)
    if not found:
        raise ValueError("未找到指定章节：" + str(scope))
    return found


def _atoms(body):
    cursor, atoms = 0, []
    for block in split_tex_blocks(body):
        value = block["tex"]
        start = body.find(value, cursor)
        if start < 0:
            raise ValueError("无法定位原始段落")
        atoms.append(dict(start=start, end=start + len(value), text=value, kind=block["kind"]))
        cursor = start + len(value)
    # Whitespace belongs to its preceding atomic unit. Thus selecting every
    # change reconstructs the exact candidate, while partial edits retain the
    # other units' original separators.
    for index, atom in enumerate(atoms):
        atom["end"] = atoms[index + 1]["start"] if index + 1 < len(atoms) else len(body)
        atom["text"] = body[atom["start"]:atom["end"]]
    return atoms


def _alignment(old, new):
    """Monotone paragraph alignment, avoiding one giant replacement opcode."""
    n, m = len(old), len(new)
    cost = [[0.0] * (m + 1) for _ in range(n + 1)]
    path = {}
    for i in range(1, n + 1):
        cost[i][0] = i * .56
        path[i, 0] = "delete"
    for j in range(1, m + 1):
        cost[0][j] = j * .56
        path[0, j] = "insert"
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            a, b = old[i - 1]["text"], new[j - 1]["text"]
            similarity = SequenceMatcher(None, a, b, autojunk=True).ratio()
            candidates = [(cost[i - 1][j - 1] + 1 - similarity, "pair"),
                          (cost[i - 1][j] + .56, "delete"),
                          (cost[i][j - 1] + .56, "insert")]
            cost[i][j], path[i, j] = min(candidates, key=lambda x: x[0])
    i, j, output = n, m, []
    while i or j:
        action = path[i, j]
        if action == "pair":
            output.append((action, i - 1, j - 1))
            i -= 1
            j -= 1
        elif action == "delete":
            output.append((action, i - 1, None))
            i -= 1
        else:
            output.append((action, i, j - 1))
            j -= 1
    return list(reversed(output))


def _overlap(first, second):
    """Insertions touching a replacement have no implicit left/right priority."""
    a, b = first
    c, d = second
    if a == b:
        return c <= a <= d
    if c == d:
        return a <= c <= b
    return max(a, c) < min(b, d)


def _prose(tex):
    # Used only to enforce the scope of a local suggestion, never to rewrite it.
    value = _mask_comments(tex)
    for start, end, _ in reversed(_math_spans(value)):
        value = value[:start] + " " * (end - start) + value[end:]
    value = re.sub(r"\\(?:cite\w*|eqref|ref|label|pageref)(?:\[[^\]]*\])*\{[^{}]*\}", " ", value)
    value = re.sub(r"\\[A-Za-z@]+\*?", " ", value)
    return value.replace("{", " ").replace("}", " ")


def _sentence_count(tex):
    value = _prose(tex)
    # Citation abbreviations and decimal numbers are not sentence boundaries.
    value = re.sub(r"\b(?:e\.g\.|i\.e\.|et\s+al\.|Fig\.|Figs\.|Eq\.|Eqs\.|Sec\.|Dr\.|Mr\.|Mrs\.|vs\.)",
                   "abbreviation", value, flags=re.I)
    value = re.sub(r"(?<=\d)\.(?=\d)", "decimal", value)
    pieces = re.split(r"(?:[.!?]+(?:\s+|$)|[。！？]+)", value.strip())
    return sum(bool(part.strip()) for part in pieces)


def _validate_math_markup(fragment):
    r"""A replacement cannot introduce an unmatched $ or \[ / \( delimiter."""
    value = _mask_comments(fragment)
    for start, end, _ in reversed(_math_spans(value)):
        value = value[:start] + " " * (end - start) + value[end:]
    for index, char in enumerate(value):
        if not _escaped(value, index) and (char == "$" or value[index:index + 2] in {r"\[", r"\]", r"\(", r"\)"}):
            raise ValueError("替换文字包含未配对的数学定界符，请保留完整公式")


def _local_context(tex, start, end):
    """At most one neighbouring sentence, with absolute source coordinates."""
    before = tex[max(0, start - 350):start]
    after = tex[end:min(len(tex), end + 350)]
    # Do not carry a neighbouring paragraph or section into the issue popup.
    before = re.split(r"\n\s*\n", before)[-1]
    after = re.split(r"\n\s*\n", after)[0]
    before_parts = list(re.finditer(r"[.!?。！？](?:\s+|$)", before))
    if before_parts:
        # If the edit starts immediately after a sentence, retain that sentence.
        boundary = before_parts[-2] if before_parts[-1].end() == len(before) and len(before_parts) > 1 else before_parts[-1]
        if boundary.end() < len(before):
            before = before[boundary.end():]
    match = re.search(r"[.!?。！？](?:\s+|$)", after)
    if match:
        after = after[:match.end()]
    # Headings are navigation context, not neighbouring editable prose.
    if re.search(r"\\(?:sub)*section\b", before):
        before = before[before.rfind("\n") + 1:]
    if re.search(r"\\(?:sub)*section\b", after):
        after = after[:re.search(r"\\(?:sub)*section\b", after).start()]
    return dict(before=before, original=tex[start:end], after=after, start=start, end=end)


_FLOAT = r"(?:figure|table)\*?"
_SPACE_LENGTHS = {"textfloatsep", "intextsep", "floatsep", "abovecaptionskip", "belowcaptionskip",
                  "tabcolsep", "parskip", "parindent", "columnsep", "abovedisplayskip", "belowdisplayskip"}


def _layout_dimension(value, *, spacing=False, bang=False):
    value = value.strip()
    if value.startswith("{") and value.endswith("}"):
        value = value[1:-1].strip()
    if bang and value == "!":
        return
    sign = r"[+-]?" if spacing else r"\+?"
    number = r"(?:\d+(?:\.\d*)?|\.\d+)"
    if not re.fullmatch(sign + r"(?:" + number + r"(?:pt|mm|cm|in|em|ex)|(?:" + number + r")?\\(?:textwidth|columnwidth|linewidth|textheight|baselineskip))", value):
        raise ValueError("布局尺寸仅支持数值单位或 textwidth/columnwidth/linewidth 等明确长度")
    coefficient = re.match(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", value)
    if not spacing and coefficient and float(coefficient.group()) <= 0:
        raise ValueError("图像尺寸须大于 0")


def _layout_options(options, *, check):
    kept = []
    for option in options.split(","):
        option = option.strip()
        if not option:
            continue
        key, _, value = option.partition("=")
        key, value = key.strip(), value.strip()
        if key in {"width", "height"}:
            if check:
                _layout_dimension(value)
        elif key == "scale":
            if check and (not re.fullmatch(r"(?:\d+(?:\.\d*)?|\.\d+)", value) or not 0 < float(value) <= 5):
                raise ValueError("图像 scale 须是大于 0 且不超过 5 的数值")
        elif key == "keepaspectratio":
            if check and value not in {"", "true", "false"}:
                raise ValueError("keepaspectratio 参数无效")
        else:
            # Cropping/rotation/other options are not supported changes. Existing
            # options remain in the content invariant and therefore cannot change.
            kept.append(option)
    return ",".join(kept)


def _braced_value(text, start):
    while start < len(text) and text[start].isspace():
        start += 1
    if start >= len(text) or text[start] != "{":
        raise ValueError("布局命令缺少完整参数")
    depth = 1
    cursor = start + 1
    while cursor < len(text):
        if not _escaped(text, cursor):
            if text[cursor] == "{":
                depth += 1
            elif text[cursor] == "}":
                depth -= 1
                if not depth:
                    return text[start + 1:cursor], cursor + 1
        cursor += 1
    raise ValueError("布局命令参数未闭合")


def _layout_content(value, *, check):
    """Remove only supported typography; every remaining content token must agree."""
    value = _mask_comments(value)
    # Explicit resizebox wrappers may change width while preserving the exact body.
    cursor, pieces = 0, []
    while True:
        match = re.search(r"\\resizebox\*?\s*(?=\{)", value[cursor:])
        if not match:
            pieces.append(value[cursor:])
            break
        start, stop = cursor + match.start(), cursor + match.end()
        width, stop = _braced_value(value, stop)
        height, stop = _braced_value(value, stop)
        body, stop = _braced_value(value, stop)
        if check:
            _layout_dimension(width, bang=True)
            _layout_dimension(height, bang=True)
            if width.strip() == height.strip() == "!":
                raise ValueError("resizebox 需要至少一个明确尺寸")
        pieces += [value[cursor:start], _layout_content(body, check=check)]
        cursor = stop
    value = "".join(pieces)

    def graphics(match):
        options = _layout_options(match.group(1) or "", check=check)
        return r"\includegraphics[" + options + "]{" + match.group(2) + "}"
    value = re.sub(r"\\includegraphics\*?\s*(?:\[([^\]]*)\])?\s*\{([^{}]+)\}", graphics, value)

    def placement(match):
        if check and match.group(3) is not None and not re.fullmatch(r"[htbp!H\s]+", match.group(3)):
            raise ValueError("浮动位置仅支持 h/t/b/p/!/H")
        return "\\" + match.group(1) + "{" + match.group(2).rstrip("*") + "} "
    value = re.sub(r"\\(begin|end)\s*\{(" + _FLOAT + r")\}\s*(?:\[([^\]]*)\])?", placement, value)

    def spacing(match):
        if check:
            _layout_dimension(match.group(1), spacing=True)
        return " "
    value = re.sub(r"\\(?:vspace|hspace)\*?\s*\{([^{}]+)\}", spacing, value)

    def setlength(match):
        if match.group(1) not in _SPACE_LENGTHS:
            return match.group()
        if check:
            _layout_dimension(match.group(2), spacing=True)
        return " "
    value = re.sub(r"\\setlength\s*\{\s*\\([A-Za-z]+)\s*\}\s*\{([^{}]+)\}", setlength, value)

    def stretch(match):
        if check and not .1 <= float(match.group(1)) <= 5:
            raise ValueError("arraystretch 应在 0.1 至 5 之间")
        return " "
    value = re.sub(r"\\renewcommand\s*\{\s*\\arraystretch\s*\}\s*\{(\d+(?:\.\d*)?|\.\d+)\}", stretch, value)
    value = re.sub(r"\\(?:centering|raggedright|raggedleft)\b|\\(?:begin|end)\s*\{(?:center|flushleft|flushright)\}", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _validate_layout_edit(tex, edit):
    old, new = edit["original_tex"], edit["proposed_tex"]
    start, end = edit["start"], edit["end"]
    if len(old) > 20000 or len(new) > 20000:
        raise ValueError("单处布局建议过长，请按单个图表分别处理")
    # A placement/graphics-options fragment is safe only at its actual command.
    for match in re.finditer(r"\\begin\s*\{" + _FLOAT + r"\}\s*(\[[^\]]*\])?", _mask_comments(tex)):
        a, b = match.span(1) if match.group(1) is not None else (match.end(), match.end())
        if (start, end) == (a, b):
            if new and not re.fullmatch(r"\[[htbp!H\s]+\]", new.strip()):
                raise ValueError("浮动位置仅支持 h/t/b/p/!/H")
            return
    for match in re.finditer(r"\\includegraphics\*?\s*(\[[^\]]*\])?\s*\{[^{}]+\}", _mask_comments(tex)):
        if match.group(1) is not None and (start, end) == match.span(1):
            if not re.fullmatch(r"\[[^\]]*\]", new.strip()):
                raise ValueError("图像参数须保持完整方括号")
            before = _layout_options(old.strip()[1:-1], check=False)
            after = _layout_options(new.strip()[1:-1], check=True)
            if before != after:
                raise ValueError("布局首版仅支持图像尺寸，裁剪或旋转参数须另行人工处理")
            return
    # Remove dimensions and spacing only after exact content preservation checks.
    if _layout_content(old, check=False) != _layout_content(new, check=True):
        raise ValueError("布局建议只能改变位置、尺寸、间距或对齐；正文、表格数据、标题、标签与图像路径必须保持")
    old_float = re.fullmatch(r"\s*\\begin\s*\{(" + _FLOAT + r")\}[\s\S]*\\end\s*\{\1\}\s*", old)
    new_float = re.fullmatch(r"\s*\\begin\s*\{(" + _FLOAT + r")\}[\s\S]*\\end\s*\{\1\}\s*", new)
    if old_float or new_float:
        if not old_float or not new_float or old_float.group(1).rstrip("*") != new_float.group(1).rstrip("*"):
            raise ValueError("请保留同一个完整 figure/table 环境")
        return
    # Without a whole float, allow only one explicit recognized layout command,
    # or insertion/deletion of such a command. Arbitrary prose is not a layout edit.
    simple = r"(?:\\(?:vspace|hspace)\*?\s*\{[^{}]+\}|\\setlength\s*\{\\[A-Za-z]+\}\s*\{[^{}]+\}|\\renewcommand\s*\{\\arraystretch\}\s*\{[^{}]+\}|\\(?:centering|raggedright|raggedleft)\b|\\includegraphics\*?\s*(?:\[[^\]]*\])?\s*\{[^{}]+\})"
    if all(not value.strip() or re.fullmatch(r"\s*" + simple + r"\s*", value) for value in (old, new)):
        return
    raise ValueError("此排版语法暂不支持自动处理；请使用完整图表、浮动位置、图像尺寸、明确间距或对齐命令")


class SourceReviewStore:
    def __init__(self, root, store=None, manuscript=None):
        self.root = Path(root).resolve()
        self.store = store or Store(self.root)
        self.manuscript = manuscript
        with self.store.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS source_documents (
              project_id TEXT PRIMARY KEY,tex TEXT NOT NULL,sha256 TEXT NOT NULL,
              revision INTEGER NOT NULL,original_path TEXT NOT NULL,candidate_path TEXT NOT NULL,
              assets_path TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS source_versions (
              id TEXT PRIMARY KEY,project_id TEXT NOT NULL,revision INTEGER NOT NULL,
              tex TEXT NOT NULL,sha256 TEXT NOT NULL,actor TEXT NOT NULL,reason TEXT NOT NULL,
              path TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(project_id,revision));
            CREATE TABLE IF NOT EXISTS source_proposals (
              id TEXT PRIMARY KEY,project_id TEXT NOT NULL,title TEXT NOT NULL,section_id TEXT NOT NULL,
              reason TEXT NOT NULL,status TEXT NOT NULL,start INTEGER NOT NULL,end INTEGER NOT NULL,
              original_tex TEXT NOT NULL,candidate_tex TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS source_hunks (
              id TEXT PRIMARY KEY,proposal_id TEXT NOT NULL,ordinal INTEGER NOT NULL,
              title TEXT NOT NULL,kind TEXT NOT NULL,reason TEXT NOT NULL,
              start INTEGER NOT NULL,end INTEGER NOT NULL,original_tex TEXT NOT NULL,
              proposed_tex TEXT NOT NULL,status TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS source_proposal_project ON source_proposals(project_id,status);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(source_proposals)")}
            if "review_mode" not in columns:
                db.execute("ALTER TABLE source_proposals ADD COLUMN review_mode TEXT NOT NULL DEFAULT 'section'")
            hunk_columns = {row[1] for row in db.execute("PRAGMA table_info(source_hunks)")}
            if "metadata_json" not in hunk_columns:
                db.execute("ALTER TABLE source_hunks ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'")

    def _project(self, pid):
        if not isinstance(pid, str) or not re.fullmatch(r"project_[A-Za-z0-9_]+", pid) or not self.store.project(pid):
            raise ValueError("课题不存在")
        return pid

    def active(self, pid):
        return bool(self.store.query("SELECT project_id FROM source_documents WHERE project_id=?", (pid,), one=True))

    def _folder(self, pid):
        return self.root / "projects" / pid / "source_review"

    def _version(self, db, pid, revision, tex, actor, reason):
        folder = self._folder(pid) / "versions"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"v{revision:06d}_{digest(tex)[:12]}.tex"
        if not path.exists():
            with path.open("x", encoding="utf-8", newline="\n") as fh:
                fh.write(tex)
        elif path.read_text(encoding="utf-8") != tex:
            raise ValueError("不可变版本指纹不一致")
        db.execute("INSERT INTO source_versions VALUES(?,?,?,?,?,?,?,?,?)",
                   (uid("sourceversion"), pid, revision, tex, digest(tex), actor, reason,
                    path.relative_to(self.root).as_posix(), now()))

    def _doc(self, db, pid):
        row = db.execute("SELECT * FROM source_documents WHERE project_id=?", (pid,)).fetchone()
        if row is None:
            raise ValueError("原稿审阅尚未初始化")
        return dict(row)

    def _check(self, doc, base_hash):
        if not isinstance(base_hash, str) or base_hash != doc["sha256"]:
            raise ManuscriptConflict("稿件已变化，请读取当前原文后再操作；未覆盖任何文字",
                                     dict(sha256=doc["sha256"], revision=doc["revision"]))

    @staticmethod
    def _status(db, proposal_id):
        statuses = [r[0] for r in db.execute("SELECT status FROM source_hunks WHERE proposal_id=?", (proposal_id,))]
        if "pending" in statuses:
            status = "partially_accepted" if "accepted" in statuses else "pending"
        elif "superseded" in statuses:
            status = "superseded"
        elif "stale" in statuses:
            status = "stale"
        elif "accepted" in statuses:
            status = "accepted"
        else:
            status = "rejected"
        db.execute("UPDATE source_proposals SET status=?,updated_at=? WHERE id=?", (status, now(), proposal_id))

    def _add_hunk(self, db, proposal_id, ordinal, title, kind, reason, start, end, old, new, metadata=None):
        if old == new:
            return
        db.execute("INSERT INTO source_hunks (id,proposal_id,ordinal,title,kind,reason,start,end,original_tex,proposed_tex,status,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (uid("hunk"), proposal_id, ordinal, title, kind, reason, start, end, old, new, "pending",
                    json.dumps(metadata or {}, ensure_ascii=False)))

    def _create_proposal(self, db, pid, current, candidate, reason, scope):
        area = _scope(current, scope)
        try:
            new_area = _scope(candidate, scope)
            replacement = candidate[new_area["start"]:new_area["end"]]
        except ValueError:
            replacement = candidate
            if area["level"] == 1 and not re.match(r"\s*\\section", replacement):
                replacement = current[area["start"]:area["heading_end"]] + "\n\n" + replacement
        original = current[area["start"]:area["end"]]
        if original == replacement:
            return None
        proposal_id, stamp = uid("sourceproposal"), now()
        db.execute("INSERT INTO source_proposals (id,project_id,title,section_id,reason,status,start,end,"
                   "original_tex,candidate_tex,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (proposal_id, pid, area["title"], area["id"], reason, "pending",
                    area["start"], area["end"], original, replacement, stamp, stamp))
        old_children = _headings(original, "subsection")
        new_children = _headings(replacement, "subsection")
        if area["level"] == 1 and old_children and new_children:
            def units(text, headings):
                output = []
                for i, h in enumerate(headings):
                    end = headings[i + 1]["start"] if i + 1 < len(headings) else len(text)
                    label = re.search(r"\\label\s*\{([^{}]+)\}", text[h["start"]:h["end"]])
                    output.append(dict(key=label.group(1) if label else h["title"].lower(),
                                       start=h["start"], end=end, title=h["title"], text=text[h["start"]:end]))
                return output
            old_units, new_units = units(original, old_children), units(replacement, new_children)
            old_by_key = {x["key"]: x for x in old_units}
            new_by_key = {x["key"]: x for x in new_units}
            ordinal = 0
            # Include any change to the method introduction/section heading.
            old_lead, new_lead = original[:old_children[0]["start"]], replacement[:new_children[0]["start"]]
            self._add_hunk(db, proposal_id, ordinal, "章节引入与标题", "prose", reason,
                           area["start"], area["start"] + len(old_lead), old_lead, new_lead)
            for index, part in enumerate(new_units):
                ordinal += 1
                old = old_by_key.get(part["key"])
                if old:
                    start, end, before = old["start"], old["end"], old["text"]
                else:
                    # New subsection is inserted before the next existing subsection.
                    following = next((old_by_key[x["key"]] for x in new_units[index + 1:]
                                      if x["key"] in old_by_key), None)
                    start = end = following["start"] if following else len(original)
                    before = ""
                self._add_hunk(db, proposal_id, ordinal, part["title"], "subsection", reason,
                               area["start"] + start, area["start"] + end, before, part["text"])
            for old in old_units:
                if old["key"] not in new_by_key:
                    ordinal += 1
                    self._add_hunk(db, proposal_id, ordinal, "删除：" + old["title"], "subsection", reason,
                                   area["start"] + old["start"], area["start"] + old["end"], old["text"], "")
        else:
            old_atoms, new_atoms = _atoms(original), _atoms(replacement)
            ordinal = 0
            for action, oi, ni in _alignment(old_atoms, new_atoms):
                if action == "pair":
                    old, new = old_atoms[oi], new_atoms[ni]
                    start, end, before, after = old["start"], old["end"], old["text"], new["text"]
                    kind = "math" if old["kind"] == "math" or new["kind"] == "math" else "prose"
                elif action == "delete":
                    old = old_atoms[oi]
                    start, end, before, after, kind = old["start"], old["end"], old["text"], "", old["kind"]
                else:
                    new = new_atoms[ni]
                    start = end = old_atoms[oi]["start"] if oi < len(old_atoms) else len(original)
                    before, after, kind = "", new["text"], new["kind"]
                if before == after:
                    continue
                ordinal += 1
                self._add_hunk(db, proposal_id, ordinal,
                               ("公式" if kind == "math" else "修改") + f" {ordinal}", kind, reason,
                               area["start"] + start, area["start"] + end, before, after)
        if not db.execute("SELECT 1 FROM source_hunks WHERE proposal_id=?", (proposal_id,)).fetchone():
            db.execute("DELETE FROM source_proposals WHERE id=?", (proposal_id,))
            return None
        return proposal_id

    def initialize_upload(self, pid, original_path, assets_path):
        self._project(pid)
        original=contained_path(self.root,str(original_path))
        assets=contained_path(self.root,str(assets_path),directory=True)
        tex=normalized(original.read_text(encoding='utf-8-sig'));validate_source(tex)
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM source_documents WHERE project_id=?',(pid,)).fetchone():raise ManuscriptConflict('课题已有稿件，不能覆盖')
            path=original.relative_to(self.root).as_posix();stamp=now()
            db.execute('INSERT INTO source_documents VALUES(?,?,?,?,?,?,?,?,?)',(pid,tex,digest(tex),1,path,path,assets.relative_to(self.root).as_posix(),stamp,stamp))
            self._version(db,pid,1,tex,'author_zip_upload','作者上传的 ZIP 工程；章节已展开为可审阅正文')
        return self.get(pid)

    def prepare_review(self, pid, original_path, candidate_path, assets_path=None, expected_hash=None):
        self._project(pid)
        original = contained_path(self.root, original_path)
        candidate = contained_path(self.root, candidate_path)
        assets = contained_path(self.root, assets_path or str(candidate.parent), directory=True)
        original_tex = normalized(original.read_text(encoding="utf-8-sig"))
        candidate_tex = normalized(candidate.read_text(encoding="utf-8-sig"))
        validate_source(original_tex)
        validate_source(candidate_tex)
        with self.store.lock:
            if self.active(pid):
                doc = self.get(pid)
                if doc.get("original_sha256") == digest(original_tex):
                    return doc
                raise ManuscriptConflict("已有审阅底稿；不会重置作者的后续修改")
            previous = self.manuscript.export_tex(pid)["tex"] if self.manuscript and self.manuscript.get(pid) else candidate_tex
            previous = normalized(previous)
            if expected_hash and digest(previous) != expected_hash:
                raise ManuscriptConflict("准备审阅前稿件已变化，当前版本已保留，请重新读取")
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT 1 FROM source_documents WHERE project_id=?", (pid,)).fetchone():
                    raise ManuscriptConflict("审阅已初始化")
                stamp = now()
                db.execute("INSERT INTO source_documents VALUES(?,?,?,?,?,?,?,?,?)",
                           (pid, original_tex, digest(original_tex), 2, str(original.relative_to(self.root)),
                            str(candidate.relative_to(self.root)), str(assets.relative_to(self.root)), stamp, stamp))
                self._version(db, pid, 1, previous, "migration_archive",
                              "Archived previous working draft before restoring the author original")
                self._version(db, pid, 2, original_tex, "user_requested_baseline_restore",
                              "Author original restored; proposed revisions require explicit acceptance")
                self._create_proposal(db, pid, original_tex, candidate_tex,
                    "按已确定的六段逻辑重组：无造影剂任务—混杂困难—建模需求—先验分工—生成读出联系—统一方法。", "introduction")
                self._create_proposal(db, pid, original_tex, candidate_tex,
                    "按作者确定的层次先验、条件残差与最终 cine 状态恢复重构推导。小节可分步采纳；跨节符号与实验引用随后核对。", "method")
        return self.get(pid)

    def _files(self, doc):
        prefix = "/api/source/" + doc["project_id"]
        files = [dict(name="main.tex", kind="tex", editable=True, url=prefix + "/file?name=main.tex")]
        folder = contained_path(self.root, doc["assets_path"], directory=True)
        for path in sorted(folder.rglob('*'), key=lambda p: p.as_posix().lower()):
            if not path.is_file() or path.suffix.lower() not in ASSET_EXTENSIONS or path.suffix.lower() == ".tex":
                continue
            name=path.relative_to(folder).as_posix()
            files.append(dict(name=name, kind=path.suffix.lower().lstrip("."), editable=False,
                              url=prefix + "/file?name=" + urllib.parse.quote(name)))
        return files

    def _proposal(self, db, doc, pid):
        row = db.execute("SELECT * FROM source_proposals WHERE id=? AND project_id=?", (pid, doc["project_id"])).fetchone()
        if row is None:
            raise ValueError("修改建议不存在")
        result = dict(row)
        hunks = [dict(h) for h in db.execute("SELECT * FROM source_hunks WHERE proposal_id=? ORDER BY ordinal,id", (pid,))]
        by_ordinal = {h["ordinal"]: h for h in hunks}
        for h in hunks:
            metadata = json.loads(h.pop("metadata_json", "{}"))
            dependencies = [by_ordinal[o] for o in metadata.get("depends_on_ordinals", []) if o in by_ordinal]
            valid = h["status"] == "pending" and doc["tex"][h["start"]:h["end"]] == h["original_tex"]
            h.update(selectable=valid, depends_on=[d["id"] for d in dependencies],
                     depends_on_titles=[d["title"] for d in dependencies],
                     dependency_statuses={d["id"]: d["status"] for d in dependencies},
                     current_start=h["start"] if valid else None,
                     current_end=h["end"] if valid else None, offset_valid=valid)
        result["hunks"] = hunks
        result["base_hash"] = doc["sha256"]
        result["original_tex"] = doc["tex"][result["start"]:result["end"]]
        result["baseline_tex"] = row["original_tex"]
        selected = [h for h in hunks if h["status"] == "pending"]
        value = result["original_tex"]
        for h in sorted(selected, key=lambda h: (h["start"], h["end"], h["ordinal"]), reverse=True):
            start, end = h["start"] - result["start"], h["end"] - result["start"]
            if start >= 0 and end <= len(value):
                value = value[:start] + h["proposed_tex"] + value[end:]
        result["proposed_tex"] = value
        result["context"] = _local_context(doc["tex"], result["start"], result["end"])
        result["edits"] = [dict(h) for h in hunks if h["status"] == "pending"]
        result["active"] = result["status"] in ACTIVE and any(h["selectable"] for h in hunks)
        result["read_only"] = not result["active"]
        return result

    def get(self, pid):
        self._project(pid)
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM source_documents WHERE project_id=?", (pid,)).fetchone()
            if not row:
                return dict(project_id=pid, initialized=False, review_mode=True, tex="", sha256="", hash="",
                            revision=0, files=[], outline=[], proposals=[], preview_url=None)
            doc = dict(row)
            doc["proposals"] = [self._proposal(db, doc, p[0]) for p in
                                db.execute("SELECT id FROM source_proposals WHERE project_id=? ORDER BY created_at,id", (pid,))]
            doc["versions"] = [dict(v) for v in db.execute(
                "SELECT revision,sha256,actor,reason,path,created_at FROM source_versions WHERE project_id=? ORDER BY revision DESC", (pid,))]
        doc.update(hash=doc["sha256"], initialized=True, review_mode=True, source_type="author_original_review",
                   original_sha256=digest(normalized((self.root / doc["original_path"]).read_text(encoding="utf-8-sig"))),
                   files=self._files(doc), outline=source_outline(doc["tex"]), risks=risks(doc["tex"]),
                   preview_url=None, source_note="原文为当前底稿；红蓝修订仅是待审建议，选择接受后才写入。")
        return doc

    def proposal(self, pid, proposal_id):
        with self.store.connect() as db:
            return self._proposal(db, self._doc(db, pid), proposal_id)

    def propose(self, pid, proposed_tex, reason, base_hash, scope="document"):
        self._project(pid)
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("建议需要说明修改理由")
        with self.store.lock, self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            doc = self._doc(db, pid)
            self._check(doc, base_hash)
            area = _scope(doc["tex"], scope)
            # Check the fragment in its full document before storing, not executing it.
            try:
                new_area = _scope(proposed_tex, scope)
                fragment = proposed_tex[new_area["start"]:new_area["end"]]
            except ValueError:
                fragment = proposed_tex
                if area["level"] == 1 and not re.match(r"\s*\\section", fragment):
                    fragment = doc["tex"][area["start"]:area["heading_end"]] + "\n\n" + fragment
            preview = doc["tex"][:area["start"]] + fragment + doc["tex"][area["end"]:]
            validate_source(preview)
            proposal_id = self._create_proposal(db, pid, doc["tex"], proposed_tex, reason, scope)
        result = self.get(pid)
        result["created_proposal_id"] = proposal_id
        return result

    @staticmethod
    def _local_issue(tex, issue):
        """Validate exact edits without manufacturing word diffs from a rewrite."""
        if not isinstance(issue, dict) or set(issue) - {"title", "reason", "scope", "edits", "kind"}:
            raise ValueError("局部问题包含未知字段")
        title, reason = issue.get("title"), issue.get("reason")
        if not isinstance(title, str) or not title.strip() or len(title) > 240:
            raise ValueError("局部问题需要不超过 240 字符的标题")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 6000:
            raise ValueError("局部问题需要不超过 6000 字符的具体修改理由")
        scope = issue.get("scope", "document")
        if not isinstance(scope, str):
            raise ValueError("局部问题 scope 必须为章节编号或标题")
        area = _scope(tex, scope)
        mode = issue.get("kind", "local")
        if mode not in {"local", "structural", "layout"}:
            raise ValueError("问题 kind 只支持 local、layout 或 structural")
        edits = issue.get("edits")
        if not isinstance(edits, list) or not 1 <= len(edits) <= (32 if mode in {"structural", "layout"} else 8):
            raise ValueError("每个局部问题需要 1–8 处明确修改；结构建议最多 32 处")
        checked, spans = [], _math_spans(_mask_comments(tex))
        for edit in edits:
            if not isinstance(edit, dict) or set(edit) - {"start", "end", "original_tex", "proposed_tex", "reason", "title", "depends_on_ordinals"}:
                raise ValueError("局部修改包含未知字段")
            start, end = edit.get("start"), edit.get("end")
            before, after = edit.get("original_tex"), edit.get("proposed_tex")
            if type(start) is not int or type(end) is not int or not area["start"] <= start <= end <= area["end"]:
                raise ValueError("局部修改起止位置超出指定章节或无效")
            if not isinstance(before, str) or not isinstance(after, str) or len(after) > 20000:
                raise ValueError("局部修改需要原文和不超过 20000 字符的替换文字")
            if before != tex[start:end]:
                raise ManuscriptConflict("局部修改与当前原文不一致，请重新定位；未写入任何建议")
            if before == after:
                raise ValueError("局部修改不能与原文相同")
            _validate_math_markup(after)
            why = edit.get("reason", reason)
            if not isinstance(why, str) or not why.strip() or len(why) > 6000:
                raise ValueError("每处修改需要有效理由")
            for a, b, _ in spans:
                touches = max(start, a) < min(end, b) or (start == end and a < start < b)
                if touches and not (start <= a and end >= b):
                    raise ValueError("公式必须作为完整数学单元修改，不能切开符号或环境")
            item = dict(start=start, end=end, original_tex=before, proposed_tex=after, reason=why)
            item_title = edit.get("title", title)
            dependencies = edit.get("depends_on_ordinals", [])
            if not isinstance(item_title, str) or not item_title.strip() or len(item_title) > 240:
                raise ValueError("修改单元需要明确标题")
            if (not isinstance(dependencies, list) or len(set(dependencies)) != len(dependencies)
                    or any(type(o) is not int or not 1 <= o <= len(edits) for o in dependencies)):
                raise ValueError("结构依赖须引用有效的修改单元序号")
            item.update(title=item_title, depends_on_ordinals=dependencies)
            if mode == "layout":
                _validate_layout_edit(tex, item)
            if any(_overlap((start, end), (h["start"], h["end"])) for h in checked):
                raise ValueError("同一问题的修改范围重叠，或插入位置存在歧义")
            checked.append(item)
        checked.sort(key=lambda e: (e["start"], e["end"]))
        if any(o >= index for index, h in enumerate(checked, 1) for o in h["depends_on_ordinals"]):
            raise ValueError("结构依赖必须指向文稿中先前的单元，不能循环或指向自身")
        start, end = min(h["start"] for h in checked), max(h["end"] for h in checked)
        original = tex[start:end]
        replacement = SourceReviewStore._apply(original, [
            {**h, "start": h["start"] - start, "end": h["end"] - start, "ordinal": i}
            for i, h in enumerate(checked)])
        if mode == "local":
            # A whole formula is an atom even when its LaTeX spans many lines.
            only_math = (not _prose(original).strip() and not _prose(replacement).strip() and
                         all(not _prose(h["original_tex"]).strip() and
                             not _prose(h["proposed_tex"]).strip() for h in checked))
            if only_math:
                if len(original) > 12000 or len(replacement) > 12000:
                    raise ValueError("数学建议过长，请按完整公式分别说明问题")
                if any(len(_math_spans(h[field])) > 1 for h in checked for field in ("original_tex", "proposed_tex")):
                    raise ValueError("每处数学修改只能覆盖一个完整公式；多个公式请分别列为修改")
            else:
                for value in (original, replacement):
                    prose = _prose(value)
                    words = re.findall(r"[A-Za-z0-9_]+|[\u3400-\u9fff]", prose)
                    if (len(value) > 2000 or len(words) > 180 or _sentence_count(value) > 2 or
                            re.search(r"\n[ \t]*\n", prose)):
                        raise ValueError("局部问题请限定为同段内不超过两句的具体词句；整段或结构重写请单列结构建议")
        # Check each independent choice and the combined choice in document context.
        # This prevents two edits that only balance braces when accepted together.
        for h in checked:
            validate_source(tex[:h["start"]] + h["proposed_tex"] + tex[h["end"]:])
        validate_source(tex[:start] + replacement + tex[end:])
        return dict(title=title, reason=reason, section_id=area["id"], review_mode=mode,
                    start=start, end=end, original_tex=original, candidate_tex=replacement, edits=checked)

    def local_edits(self, pid, issues, base_hash, supersede_proposal_ids=None):
        """Create one independent review per exact issue; never edit the manuscript."""
        self._project(pid)
        if not isinstance(issues, list) or not 1 <= len(issues) <= 100:
            raise ValueError("一次需要 1–100 个具体局部问题")
        supersede = [] if supersede_proposal_ids is None else supersede_proposal_ids
        if (not isinstance(supersede, list) or any(not isinstance(x, str) for x in supersede) or
                len(set(supersede)) != len(supersede)):
            raise ValueError("被取代建议的编号列表无效")
        created = []
        with self.store.lock, self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            doc = self._doc(db, pid)
            self._check(doc, base_hash)
            prepared = [self._local_issue(doc["tex"], issue) for issue in issues]
            ranges = []
            for issue in prepared:
                for h in issue["edits"]:
                    pair = h["start"], h["end"]
                    if any(_overlap(pair, previous) for previous in ranges):
                        raise ValueError("不同问题的修改范围重叠，或在同一点插入，需明确分开")
                    ranges.append(pair)
            for sid in supersede:
                row = db.execute("SELECT * FROM source_proposals WHERE id=? AND project_id=?", (sid, pid)).fetchone()
                pending_old = db.execute("SELECT start,end FROM source_hunks WHERE proposal_id=? AND status='pending'", (sid,)).fetchall()
                structural_ranges = [(h["start"], h["end"]) for issue in prepared if issue["review_mode"] == "structural" for h in issue["edits"]]
                covered_local = (row is not None and row["review_mode"] == "local" and pending_old
                                 and all(any(a <= h["start"] <= h["end"] <= b for a,b in structural_ranges) for h in pending_old))
                if row is None or (row["review_mode"] in {"local", "layout"} and not covered_local):
                    raise ValueError("只能取代同一课题的旧范围建议，不能删除局部问题或其他课题历史")
            pending_sql = ("SELECT h.start,h.end FROM source_hunks h JOIN source_proposals p ON p.id=h.proposal_id "
                           "WHERE p.project_id=? AND p.review_mode IN ('local','layout') AND h.status='pending'")
            pending_args = [pid]
            if supersede:
                pending_sql += " AND p.id NOT IN (" + ",".join("?" for _ in supersede) + ")"
                pending_args.extend(supersede)
            pending = db.execute(pending_sql, pending_args).fetchall()
            if any(_overlap(pair, (h["start"], h["end"])) for pair in ranges for h in pending):
                raise ManuscriptConflict("该位置已有待审局部问题，请先处理现有问题，避免重复或冲突建议")
            # No inserts occur before every issue, collision and history id validates.
            for issue in prepared:
                proposal_id, stamp = uid("sourceproposal"), now()
                db.execute("INSERT INTO source_proposals (id,project_id,title,section_id,reason,status,start,end,"
                           "original_tex,candidate_tex,created_at,updated_at,review_mode) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (proposal_id, pid, issue["title"], issue["section_id"], issue["reason"], "pending",
                            issue["start"], issue["end"], issue["original_tex"], issue["candidate_tex"], stamp, stamp,
                            issue["review_mode"]))
                for ordinal, h in enumerate(issue["edits"], 1):
                    kind = ("layout" if issue["review_mode"] == "layout" else
                            "math" if _math_spans(h["original_tex"]) or _math_spans(h["proposed_tex"]) else "prose")
                    self._add_hunk(db, proposal_id, ordinal, h["title"], kind, h["reason"],
                                   h["start"], h["end"], h["original_tex"], h["proposed_tex"],
                                   {"depends_on_ordinals": h["depends_on_ordinals"]})
                created.append(proposal_id)
            for sid in supersede:
                db.execute("UPDATE source_hunks SET status='superseded' WHERE proposal_id=? AND status='pending'", (sid,))
                self._status(db, sid)
        result = self.get(pid)
        result["created_proposal_ids"] = created
        return result

    def propose_local_issues(self, pid, issues, base_hash, scope=None, supersede_proposal_ids=None):
        if scope is not None and isinstance(issues, list):
            issues = [{"scope": scope, **issue} if isinstance(issue, dict) else issue for issue in issues]
        return self.local_edits(pid, issues, base_hash, supersede_proposal_ids)

    def _selected(self, db, doc, proposal_id, selected_ids=None):
        row = db.execute("SELECT * FROM source_proposals WHERE id=? AND project_id=?", (proposal_id, doc["project_id"])).fetchone()
        if row is None:
            raise ValueError("修改建议不存在")
        pending = [dict(h) for h in db.execute("SELECT * FROM source_hunks WHERE proposal_id=? AND status='pending' ORDER BY ordinal", (proposal_id,))]
        if selected_ids is None:
            chosen = pending
        else:
            if not isinstance(selected_ids, list) or not selected_ids or any(not isinstance(x, str) for x in selected_ids):
                raise ValueError("请选择至少一处待处理修改")
            known = {h["id"] for h in pending}
            if len(set(selected_ids)) != len(selected_ids) or not set(selected_ids) <= known:
                raise ManuscriptConflict("选中的建议已处理或不属于当前建议")
            chosen = [h for h in pending if h["id"] in selected_ids]
        if not chosen:
            raise ManuscriptConflict("该建议没有待处理修改")
        for h in chosen:
            if doc["tex"][h["start"]:h["end"]] != h["original_tex"]:
                raise ManuscriptConflict("原段落已变化，需重新生成此处建议")
        return dict(row), chosen

    @staticmethod
    def _apply(tex, hunks):
        for h in sorted(hunks, key=lambda h: (h["start"], h["end"], h["ordinal"]), reverse=True):
            tex = tex[:h["start"]] + h["proposed_tex"] + tex[h["end"]:]
        return tex

    @staticmethod
    def _position(position, edits, right=False):
        shift = 0
        for start, end, replacement in edits:
            if end < position or (end == position and (start != end or right)):
                shift += len(replacement) - (end - start)
            elif start < position < end:
                return start + shift + (len(replacement) if right else 0)
        return position + shift

    def _rebase(self, db, pid, edits):
        for row in db.execute("SELECT h.* FROM source_hunks h JOIN source_proposals p ON p.id=h.proposal_id WHERE p.project_id=? AND h.status='pending'", (pid,)).fetchall():
            h = dict(row)
            overlap = any((max(h["start"], a) < min(h["end"], b)) or
                          (a == b and h["start"] < a < h["end"]) or
                          (h["start"] == h["end"] and a < h["start"] < b)
                          for a, b, _ in edits)
            if overlap:
                db.execute("UPDATE source_hunks SET status='stale' WHERE id=?", (h["id"],))
            else:
                start = self._position(h["start"], edits, right=True)
                end = self._position(h["end"], edits, right=h["start"] == h["end"])
                db.execute("UPDATE source_hunks SET start=?,end=? WHERE id=?", (start, end, h["id"]))
        for p in db.execute("SELECT id,start,end FROM source_proposals WHERE project_id=?", (pid,)).fetchall():
            db.execute("UPDATE source_proposals SET start=?,end=? WHERE id=?",
                       (self._position(p["start"], edits), self._position(p["end"], edits, right=True), p["id"]))
            self._status(db, p["id"])

    def _save(self, db, doc, tex, actor, reason, edits=None):
        validate_source(tex)
        if tex == doc["tex"]:
            return
        if edits is None:
            edits = [(a, b, tex[c:d]) for tag, a, b, c, d in
                     SequenceMatcher(None, doc["tex"], tex, autojunk=True).get_opcodes() if tag != "equal"]
        self._rebase(db, doc["project_id"], edits)
        revision = doc["revision"] + 1
        db.execute("UPDATE source_documents SET tex=?,sha256=?,revision=?,updated_at=? WHERE project_id=?",
                   (tex, digest(tex), revision, now(), doc["project_id"]))
        self._version(db, doc["project_id"], revision, tex, actor, reason)

    def save(self, pid, tex, base_hash, reason="人工编辑"):
        self._project(pid)
        tex = normalized(tex)
        with self.store.lock, self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            doc = self._doc(db, pid)
            self._check(doc, base_hash)
            self._save(db, doc, tex, "user_manual", reason)
        return self.get(pid)

    def restore_revision(self, pid, revision, base_hash):
        self._project(pid)
        if type(revision) is not int or revision < 1:
            raise ValueError("版本编号无效")
        with self.store.lock, self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            doc = self._doc(db, pid)
            self._check(doc, base_hash)
            row = db.execute("SELECT path,sha256 FROM source_versions WHERE project_id=? AND revision=?", (pid,revision)).fetchone()
            if not row:
                raise ValueError("版本不存在")
            path = (self.root / row["path"]).resolve()
            if not path.is_relative_to(self.root):
                raise ValueError("版本路径越界")
            tex = path.read_text(encoding="utf-8")
            if digest(tex) != row["sha256"]:
                raise ValueError("历史版本校验失败")
            self._save(db, doc, tex, "user_undo", "恢复版本 " + str(revision))
        return self.get(pid)

    def preview(self, pid, proposal_id, selected_ids=None, proposed_tex=None):
        with self.store.connect() as db:
            doc = self._doc(db, pid)
            proposal, chosen = self._selected(db, doc, proposal_id, selected_ids)
            if proposed_tex is None:
                tex = self._apply(doc["tex"], chosen)
            else:
                if proposal["review_mode"] in {"local", "structural", "layout"}:
                    self._local_issue(doc["tex"], dict(title=proposal["title"], reason=proposal["reason"],
                        kind=proposal["review_mode"], edits=[dict(start=proposal["start"], end=proposal["end"],
                        original_tex=doc["tex"][proposal["start"]:proposal["end"]], proposed_tex=proposed_tex)]))
                tex = doc["tex"][:proposal["start"]] + proposed_tex + doc["tex"][proposal["end"]:]
            validate_source(tex)
        return dict(tex=tex, sha256=digest(tex), revision=doc["revision"], not_saved=True, risks=risks(tex))

    def accept(self, pid, proposal_id, base_hash, selected_ids=None, proposed_tex=None):
        with self.store.lock, self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            doc = self._doc(db, pid)
            self._check(doc, base_hash)
            proposal, chosen = self._selected(db, doc, proposal_id, selected_ids)
            if proposed_tex is None:
                tex = self._apply(doc["tex"], chosen)
                edits = [(h["start"], h["end"], h["proposed_tex"]) for h in chosen]
            else:
                if not isinstance(proposed_tex, str):
                    raise ValueError("编辑后的建议须为 LaTeX 字符串")
                if proposal["review_mode"] in {"local", "structural", "layout"}:
                    self._local_issue(doc["tex"], dict(title=proposal["title"], reason=proposal["reason"],
                        kind=proposal["review_mode"], edits=[dict(start=proposal["start"], end=proposal["end"],
                        original_tex=doc["tex"][proposal["start"]:proposal["end"]], proposed_tex=proposed_tex)]))
                tex = doc["tex"][:proposal["start"]] + proposed_tex + doc["tex"][proposal["end"]:]
                edits = [(proposal["start"], proposal["end"], proposed_tex)]
                # Explicitly edited whole-proposal acceptance supersedes pending subchoices.
                chosen = [dict(h) for h in db.execute("SELECT * FROM source_hunks WHERE proposal_id=? AND status='pending'", (proposal_id,))]
            validate_source(tex)
            for h in chosen:
                db.execute("UPDATE source_hunks SET status='accepted' WHERE id=?", (h["id"],))
            self._save(db, doc, tex, "user_accept", proposal["reason"], edits=edits)
            self._status(db, proposal_id)
        return self.get(pid)

    def reject(self, pid, proposal_id, selected_ids=None):
        with self.store.lock, self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            doc = self._doc(db, pid)
            _, chosen = self._selected(db, doc, proposal_id, selected_ids)
            for h in chosen:
                db.execute("UPDATE source_hunks SET status='rejected' WHERE id=?", (h["id"],))
            self._status(db, proposal_id)
        return self.get(pid)

    def export(self, pid):
        result = self.get(pid)
        if not result["initialized"]:
            raise ValueError("原稿审阅尚未初始化")
        doc = {key: result[key] for key in ("project_id", "tex", "sha256", "revision")}
        doc["tracked"] = False
        return doc

    def scope(self, pid, scope="document"):
        document = self.get(pid)
        area = _scope(document["tex"], scope)
        return {**area, "tex": document["tex"][area["start"]:area["end"]],
                "sha256": document["sha256"], "revision": document["revision"]}

    def legacy_document(self, pid):
        """Read-only compatibility projection; the previous block store is history."""
        doc = self.get(pid)
        sections = []
        outline = doc["outline"]
        for i, heading in enumerate(outline):
            if heading["level"] > 2:
                continue
            end = outline[i + 1]["start"] if i + 1 < len(outline) else heading["end"]
            body = doc["tex"][heading["heading_end"]:end]
            parent = next((h["id"] for h in reversed(outline[:i]) if h["level"] == 1), None)
            role = "introduction" if heading["title"].lower() == "introduction" else (
                "method" if (heading["title"].lower() in {"method", "methods", "methodology"} or parent == "sec:methodology")
                else "experiments")
            sections.append(dict(id=heading["id"], title=heading["title"], role_id=role,
                                 level=heading["level"], parent_id=parent if heading["level"] > 1 else None,
                                 source_type="author_original_review",
                                 blocks=[dict(id="sourceblock_" + digest(heading["id"] + str(k))[:16],
                                              tex=b["text"], kind=b["kind"], sha256=digest(b["text"]),
                                              section_id=heading["id"], revision=doc["revision"])
                                         for k, b in enumerate(_atoms(body))]))
        return dict(project_id=pid, revision=doc["revision"], sections=sections, suggestions=[],
                    source_type="author_original_review", source_note=doc["source_note"],
                    read_only=True, auto_review=False, source_review=True)

    def file(self, pid, name):
        doc = self.get(pid)
        if name == "main.tex":
            return dict(name=name, text=doc["tex"], sha256=doc["sha256"], editable=True)
        folder = contained_path(self.root, doc["assets_path"], directory=True)
        if not isinstance(name, str) or '\\' in name or ':' in name or '..' in Path(name).parts or Path(name).is_absolute() or Path(name).suffix.lower() not in ASSET_EXTENSIONS:
            raise ValueError("工程文件不存在")
        path = (folder / name).resolve()
        if not path.is_relative_to(folder) or not path.is_file() or path.suffix.lower() == ".tex":
            raise ValueError("工程文件不存在")
        if path.suffix.lower() in TEXT_EXTENSIONS:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            return dict(name=name, text=text, sha256=digest(text), editable=False)
        return dict(name=name, editable=False, binary=True,
                    url="/api/source/" + pid + "/asset?name=" + urllib.parse.quote(name))

    def asset(self, pid, name):
        info = self.file(pid, name)
        if not info.get("binary"):
            raise ValueError("不是图像或 PDF 文件")
        doc = self.get(pid)
        return contained_path(self.root, doc["assets_path"], directory=True) / name
