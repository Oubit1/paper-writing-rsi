"""Editable LaTeX, immutable source versions and CAS-bound expert proposals.

No TeX engine is executed here. Source provenance never implies original TeX.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re

from .store import Store, digest, now, uid
from .latex_source import (contained_path, parse_source, build_binding,
                           load_binding, render_source, public_metadata)


class LatexValidationError(ValueError):
    pass


class ManuscriptConflict(ValueError):
    def __init__(self, message, current=None):
        super().__init__(message)
        self.current = current


_DISPLAY_ENVS = {"equation","equation*","align","align*","gather","gather*","multline","multline*","displaymath","eqnarray","eqnarray*"}
_UNSAFE = {
    "input","include","includeonly","openin","openout","closein","closeout","read","readline",
    "write","immediate","special","directlua","latelua","luaexec","shellescape","shellesc",
    "csname","endcsname","catcode","scantokens","everyjob","everypar","output","shipout",
    "loop","repeat","def","gdef","xdef","edef","let","futurelet","newcommand","renewcommand",
    "providecommand","newenvironment","renewenvironment","usepackage","requirepackage",
    "documentclass","graphicspath","verbatiminput","lstinputlisting","inputminted","includepdf",
    "attachfile","pdfobj","pdfannot","pdfliteral","pdfxform","pdffiledump","pdfextension",
    "bibliography","bibliographystyle","addbibresource","import","subimport",
    "explsyntaxon","explsyntaxoff","makeatletter","makeatother",
}
_ROLE_CHAIN = {
    "introduction":{"method","related_work","experiments","figures"},
    "method":{"introduction","experiments","figures"},
    "related_work":{"introduction","method"},
    "experiments":{"introduction","method","figures"},
    "figures":{"introduction","method","experiments"},
}


def _escaped(text,index):
    count=0; pos=index-1
    while pos>=0 and text[pos]=="\\":
        count+=1; pos-=1
    return count%2==1


def _comments_removed(tex):
    lines=[]
    for line in tex.splitlines():
        cut=next((i for i,c in enumerate(line) if c=="%" and not _escaped(line,i)),len(line))
        lines.append(line[:cut])
    return "\n".join(lines)


def validate_tex(tex):
    """Reject unsafe source explicitly; never silently rewrite a formula."""
    if not isinstance(tex,str):
        raise LatexValidationError("LaTeX 内容必须是字符串")
    if len(tex)>500000:
        raise LatexValidationError("单段超过 500000 字符，请拆成多个段落")
    if any(ord(c)<32 and c not in "\n\r\t" for c in tex):
        raise LatexValidationError("LaTeX 含不可见控制字符")
    code=_comments_removed(tex)
    if "^^" in code:
        raise LatexValidationError("不接收 TeX ^^ 字符重编码")
    for command in re.findall(r"\\([A-Za-z@]+)",code):
        if command.lower().lstrip("@") in _UNSAFE:
            raise LatexValidationError("正文不能包含文件读写、执行或宏重定义命令：\\"+command)
    if re.search(r"\\(?:begin|end)\s*\{document\}",code):
        raise LatexValidationError("请编辑正文，document 外壳由导出器提供")
    images=list(re.finditer(r"\\includegraphics(?:\[[^\]]*\])?\s*\{([^{}]*)\}",code))
    for match in images:
        name=match.group(1).strip()
        if (not name or name.startswith(("/","\\")) or ":" in name or "\\" in name or
            ".." in name.split("/") or not re.fullmatch(r"[A-Za-z0-9_./ -]+\.(?:pdf|png|jpe?g|eps)",name,re.I)):
            raise LatexValidationError("图像只允许安全的相对 PDF/PNG/JPEG/EPS 文件名")
    if "\\includegraphics" in code and not images:
        raise LatexValidationError("图像引用须使用显式相对文件名")
    for match in re.finditer(r"\\href\s*\{([^{}]*)\}",code):
        if re.match(r"\s*(?:run|file|javascript|data):",match.group(1),re.I):
            raise LatexValidationError("不允许执行性或本地文件超链接")
    return tex


def _math_spans(text,include_inline=True):
    spans=[]; i=0
    while i<len(text):
        if text[i]=="%" and not _escaped(text,i):
            end=text.find("\n",i); i=len(text) if end<0 else end+1; continue
        opener=closer=None; display=False
        if text.startswith("$$",i) and not _escaped(text,i):
            opener=closer="$$"; display=True
        elif text.startswith("\\[",i) and not _escaped(text,i):
            opener,closer,display="\\[","\\]",True
        elif include_inline and text.startswith("\\(",i) and not _escaped(text,i):
            opener,closer="\\(","\\)"
        elif include_inline and text[i]=="$" and not _escaped(text,i):
            opener=closer="$"
        if opener:
            search=i+len(opener); end=-1
            while search<len(text):
                found=text.find(closer,search)
                if found<0: break
                if not _escaped(text,found):
                    end=found+len(closer); break
                search=found+len(closer)
            if end>=0 and (opener!="$" or "\n\n" not in text[i:end]):
                spans.append((i,end,display)); i=end; continue
        begin=re.match(r"\\begin\s*\{([^{}]+)\}",text[i:])
        if begin and begin.group(1) in _DISPLAY_ENVS:
            env=begin.group(1); depth=0
            for match in re.finditer(r"\\(begin|end)\s*\{"+re.escape(env)+r"\}",text[i:]):
                depth+=1 if match.group(1)=="begin" else -1
                if depth==0:
                    end=i+match.end(); spans.append((i,end,True)); i=end; break
            else:
                i+=1
            continue
        i+=1
    return spans


def _escape_plain(text):
    mapping={"\\":r"\textbackslash{}","&":r"\&","%":r"\%","$":r"\$","#":r"\#","_":r"\_",
             "{":r"\{","}":r"\}","~":r"\textasciitilde{}","^":r"\textasciicircum{}"}
    return "".join(mapping.get(c,c) for c in text)


def _command_end(text,index):
    match=re.match(r"\\(?:[A-Za-z@]+\*?|.)",text[index:],re.S)
    if not match: return index+1
    end=index+match.end()
    while end<len(text):
        argument=end
        while argument<len(text) and text[argument].isspace(): argument+=1
        if argument>=len(text) or text[argument] not in "[{": break
        opener=text[argument]; closer="]" if opener=="[" else "}"
        depth=0; cursor=argument
        while cursor<len(text):
            if text[cursor]==opener and not _escaped(text,cursor): depth+=1
            elif text[cursor]==closer and not _escaped(text,cursor):
                depth-=1
                if depth==0: end=cursor+1; break
            cursor+=1
        else: break
    return end


def _inline_markdown(text):
    spans={a:b for a,b,_ in _math_spans(text)}
    result=[]; plain=[]; i=0
    def flush():
        if plain: result.append(_escape_plain("".join(plain))); plain.clear()
    while i<len(text):
        if i in spans:
            flush(); end=spans[i]; result.append(text[i:end]); i=end; continue
        if text.startswith("**",i):
            end=text.find("**",i+2)
            if end>=0:
                flush(); result.append("\\textbf{"+_inline_markdown(text[i+2:end])+"}"); i=end+2; continue
        if text[i] in "*_" and not _escaped(text,i):
            end=text.find(text[i],i+1)
            if end>i+1 and (i==0 or not text[i-1].isalnum()) and (end+1==len(text) or not text[end+1].isalnum()):
                flush(); result.append("\\emph{"+_inline_markdown(text[i+1:end])+"}"); i=end+1; continue
        if text[i]==chr(96):
            end=text.find(chr(96),i+1)
            if end>=0:
                flush(); result.append("\\texttt{"+_escape_plain(text[i+1:end])+"}"); i=end+1; continue
        link=re.match(r"\[([^\]]+)\]\((https?://[^ )]+)\)",text[i:])
        if link:
            flush(); url=link.group(2).replace("%",r"\%").replace("#",r"\#")
            result.append("\\href{"+url+"}{"+_inline_markdown(link.group(1))+"}"); i+=link.end(); continue
        if text[i]=="\\":
            flush(); end=_command_end(text,i); result.append(text[i:end]); i=end; continue
        plain.append(text[i]); i+=1
    flush(); return "".join(result)


def _table_cells(line):
    protected={p for a,b,_ in _math_spans(line) for p in range(a,b)}
    cells=[]; start=0; code=False
    for i,c in enumerate(line):
        if c==chr(96) and i not in protected: code=not code
        if c=="|" and i not in protected and not code and not _escaped(line,i):
            cells.append(line[start:i].strip()); start=i+1
    cells.append(line[start:].strip())
    if cells and not cells[0]: cells.pop(0)
    if cells and not cells[-1]: cells.pop()
    return cells


def markdown_to_tex(markdown):
    """Convert markdown prose; preserve complete TeX math, labels and spacing."""
    if not isinstance(markdown,str): raise ValueError("Markdown 必须为字符串")
    placeholders={}; protected=markdown
    for number,(a,b,display) in reversed(list(enumerate(_math_spans(markdown)))):
        if display:
            token=f"PAPERDISPLAYMATHPLACEHOLDER{number:06d}END"
            placeholders[token]=markdown[a:b]; protected=protected[:a]+token+protected[b:]
    lines=protected.splitlines(); output=[]; i=0; list_kind=None
    def close_list():
        nonlocal list_kind
        if list_kind: output.append("\\end{"+list_kind+"}"); list_kind=None
    while i<len(lines):
        line=lines[i]; stripped=line.strip()
        if i+1<len(lines) and "|" in line and re.fullmatch(r"\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*",lines[i+1]):
            close_list(); headers=_table_cells(line); count=len(headers)
            output.extend(["\\begin{center}","\\small","\\begin{tabular}{|"+"|".join(["l"]*count)+"|}","\\hline"])
            output.append(" & ".join(_inline_markdown(c) for c in headers)+r" \\ \hline"); i+=2
            while i<len(lines) and "|" in lines[i] and lines[i].strip():
                cells=_table_cells(lines[i])
                if len(cells)!=count: break
                output.append(" & ".join(_inline_markdown(c) for c in cells)+r" \\"); i+=1
            output.extend(["\\hline","\\end{tabular}","\\end{center}"]); continue
        heading=re.match(r"^(#{1,6})\s+(.+)$",stripped)
        bullet=re.match(r"^[-*+]\s+(.+)$",stripped); numbered=re.match(r"^\d+[.)]\s+(.+)$",stripped)
        if heading:
            close_list(); command=("section","subsection","subsubsection","paragraph","subparagraph","subparagraph")[len(heading[1])-1]
            output.append("\\"+command+"{"+_inline_markdown(heading[2])+"}")
        elif bullet or numbered:
            kind="itemize" if bullet else "enumerate"
            if list_kind!=kind: close_list(); list_kind=kind; output.append("\\begin{"+kind+"}")
            output.append("\\item "+_inline_markdown((bullet or numbered)[1]))
        elif not stripped:
            close_list(); output.append("")
        elif stripped in placeholders:
            close_list(); output.append(placeholders[stripped])
        elif stripped.startswith(chr(96)*3):
            close_list(); language=stripped[3:].strip().lower(); code=[]; i+=1
            while i<len(lines) and not lines[i].strip().startswith(chr(96)*3):
                code.append(lines[i]); i+=1
            output.append("\n".join(code) if language in {"tex","latex"} else "\\begin{quote}\\ttfamily\n"+_escape_plain("\n".join(code))+"\n\\end{quote}")
        elif re.fullmatch(r"[-*_]{3,}",stripped):
            close_list(); output.append("\\par\\smallskip\\hrule\\smallskip")
        else: output.append(_inline_markdown(line))
        i+=1
    close_list(); result="\n".join(output)
    for token,math in placeholders.items(): result=result.replace(token,math)
    validate_tex(result); return result


def _block_kind(tex):
    stripped=tex.strip()
    if re.match(r"\\(?:section|subsection|subsubsection|paragraph|subparagraph)\*?\s*\{",stripped): return "heading"
    spans=_math_spans(stripped,False)
    if len(spans)==1 and spans[0][:2]==(0,len(stripped)): return "math"
    if re.match(r"\\begin\s*\{",stripped): return "raw"
    return "paragraph"


def split_tex_blocks(tex):
    validate_tex(tex)
    spans=[(a,b) for a,b,_ in _math_spans(tex,False)]
    for match in re.finditer(r"\\begin\s*\{([^{}]+)\}",tex):
        if any(a<=match.start()<b for a,b in spans): continue
        depth=0
        for end in re.finditer(r"\\(begin|end)\s*\{"+re.escape(match[1])+r"\}",tex[match.start():]):
            depth+=1 if end[1]=="begin" else -1
            if depth==0: spans.append((match.start(),match.start()+end.end())); break
    merged=[]
    for a,b in sorted(spans):
        if merged and a<merged[-1][1]: merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
        else: merged.append((a,b))
    result=[]; cursor=0
    def prose(text):
        for chunk in re.split(r"\n\s*\n|\n(?=\\(?:section|subsection|subsubsection|paragraph|subparagraph)\*?\s*\{)",text):
            chunk=chunk.strip()
            while re.match(r"\\(?:section|subsection|subsubsection|paragraph|subparagraph)\*?\s*\{",chunk):
                end=_command_end(chunk,0)
                result.append(dict(kind="heading",tex=chunk[:end]))
                chunk=chunk[end:].strip()
            if chunk: result.append(dict(kind=_block_kind(chunk),tex=chunk))
    for a,b in merged:
        prose(tex[cursor:a]); result.append(dict(kind=_block_kind(tex[a:b]),tex=tex[a:b])); cursor=b
    prose(tex[cursor:]); return result


def _diff_atoms(tex):
    math={a:b for a,b,_ in _math_spans(tex)}; atoms=[]; i=0
    while i<len(tex):
        if i in math: end=math[i]
        elif tex[i]=="\\": end=_command_end(tex,i)
        else: end=i+len(re.match(r"\s+|\w+|[^\w\s]",tex[i:],re.UNICODE)[0])
        atoms.append(tex[i:end]); i=end
    return atoms


def semantic_diff(original,proposed,atomic_math=False):
    if original==proposed: return [dict(kind="equal",text=original)] if original else []
    atomic_math=atomic_math or _block_kind(original) in {"math","raw"} or _block_kind(proposed) in {"math","raw"}
    if atomic_math: return ([dict(kind="delete",text=original)] if original else [])+([dict(kind="insert",text=proposed)] if proposed else [])
    old,new=_diff_atoms(original),_diff_atoms(proposed); result=[]
    for tag,a,b,c,d in SequenceMatcher(a=old,b=new,autojunk=False).get_opcodes():
        if tag=="equal": result.append(dict(kind="equal",text="".join(old[a:b])))
        else:
            if tag in {"delete","replace"}: result.append(dict(kind="delete",text="".join(old[a:b])))
            if tag in {"insert","replace"}: result.append(dict(kind="insert",text="".join(new[c:d])))
    return [part for part in result if part["text"]]


def _references(tex):
    return dict(
        labels=set(re.findall(r"\\label\s*\{([^{}]+)\}",tex)),
        refs=set(re.findall(r"\\(?:ref|eqref|autoref)\s*\{([^{}]+)\}",tex)),
        cites={k.strip() for group in re.findall(r"\\cite\w*\*?(?:\[[^\]]*\]){0,2}\s*\{([^{}]+)\}",tex) for k in group.split(",")},
        symbols={t for a,b,_ in _math_spans(tex) for t in re.findall(r"\\[A-Za-z]+|[A-Za-z][A-Za-z0-9_]*",tex[a:b]) if t not in {"begin","end","left","right","mathrm","mathbf","boldsymbol","text","tag","label"}},
    )


class ManuscriptStore:
    def __init__(self,root,store=None):
        self.root=Path(root).resolve(); self.store=store or Store(self.root)
        with self.store.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS manuscript_documents (
              project_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS manuscript_sections (
              project_id TEXT NOT NULL,id TEXT NOT NULL,title TEXT NOT NULL,role_id TEXT NOT NULL,
              position INTEGER NOT NULL,source_type TEXT NOT NULL,level INTEGER NOT NULL DEFAULT 1,
              parent_id TEXT,PRIMARY KEY(project_id,id));
            CREATE TABLE IF NOT EXISTS manuscript_blocks (
              id TEXT PRIMARY KEY,project_id TEXT NOT NULL,section_id TEXT NOT NULL,position INTEGER NOT NULL,
              kind TEXT NOT NULL,tex TEXT NOT NULL,revision INTEGER NOT NULL,sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS manuscript_changes (
              id TEXT PRIMARY KEY,project_id TEXT NOT NULL,block_id TEXT NOT NULL,document_revision INTEGER NOT NULL,
              original_tex TEXT NOT NULL,proposed_tex TEXT NOT NULL,before_hash TEXT NOT NULL,after_hash TEXT NOT NULL,
              actor TEXT NOT NULL,reason TEXT NOT NULL,kind TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS manuscript_suggestions (
              id TEXT PRIMARY KEY,project_id TEXT NOT NULL,block_id TEXT NOT NULL,original_tex TEXT NOT NULL,
              proposed_tex TEXT NOT NULL,reason TEXT NOT NULL,expert TEXT NOT NULL,base_hash TEXT NOT NULL,
              kind TEXT NOT NULL,severity TEXT NOT NULL,evidence TEXT NOT NULL,job_id TEXT NOT NULL,
              status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
              accepted_tex TEXT,change_id TEXT);
            CREATE TABLE IF NOT EXISTS manuscript_versions (
              project_id TEXT NOT NULL,revision INTEGER NOT NULL,json_path TEXT NOT NULL,tex_path TEXT NOT NULL,
              sha256 TEXT NOT NULL,created_at TEXT NOT NULL,actor TEXT NOT NULL,reason TEXT NOT NULL,
              PRIMARY KEY(project_id,revision));
            CREATE INDEX IF NOT EXISTS manuscript_block_order ON manuscript_blocks(project_id,section_id,position);
            CREATE INDEX IF NOT EXISTS manuscript_suggestion_target ON manuscript_suggestions(project_id,block_id,status);
            """)
            columns={row["name"] for row in db.execute("PRAGMA table_info(manuscript_sections)")}
            if "level" not in columns:
                db.execute("ALTER TABLE manuscript_sections ADD COLUMN level INTEGER NOT NULL DEFAULT 1")
            if "parent_id" not in columns:
                db.execute("ALTER TABLE manuscript_sections ADD COLUMN parent_id TEXT")

    def _project(self,pid):
        if not isinstance(pid,str) or not re.fullmatch(r"project_[A-Za-z0-9_]+",pid): raise ValueError("无效课题 ID")
        if not self.store.project(pid): raise ValueError("课题不存在")
        return pid

    def _folder(self,pid):
        self._project(pid)
        return self.root/"projects"/pid/"manuscript"

    @staticmethod
    def _suggestion(row):
        if row is None: return None
        result=dict(row); result["evidence"]=json.loads(result["evidence"])
        result["agent_proposed_tex"]=result["proposed_tex"]
        if result.get("accepted_tex") is not None: result["proposed_tex"]=result["accepted_tex"]
        result["edited_before_accept"]=result.get("accepted_tex") is not None and result["accepted_tex"]!=result["agent_proposed_tex"]
        result["diff"]=semantic_diff(result["original_tex"],result["proposed_tex"],result["kind"] in {"math","formula"})
        return result

    def _document(self,db,pid,suggestions=True,versions=True):
        row=db.execute("SELECT * FROM manuscript_documents WHERE project_id=?",(pid,)).fetchone()
        if row is None: return None
        result=dict(row); result["sections"]=[]
        for section in db.execute("SELECT * FROM manuscript_sections WHERE project_id=? ORDER BY position,id",(pid,)):
            entry=dict(section)
            entry["blocks"]=[dict(block) for block in db.execute("SELECT * FROM manuscript_blocks WHERE project_id=? AND section_id=? ORDER BY position,id",(pid,section["id"]))]
            result["sections"].append(entry)
        if suggestions:
            result["suggestions"]=[self._suggestion(s) for s in db.execute("SELECT * FROM manuscript_suggestions WHERE project_id=? ORDER BY created_at,id",(pid,))]
        if versions:
            result["versions"]=[dict(v) for v in db.execute("SELECT * FROM manuscript_versions WHERE project_id=? ORDER BY revision DESC",(pid,))]
        return result

    def get(self,project_id):
        pid=project_id
        self._project(pid)
        with self.store.connect() as db:
            db.execute("BEGIN")
            document=self._document(db,pid)
        if document is not None:
            document.update(public_metadata(load_binding(self._folder(pid))))
        return document

    def get_block(self,project_id,block_id):
        pid,bid=project_id,block_id
        self._project(pid)
        return self.store.query("SELECT * FROM manuscript_blocks WHERE project_id=? AND id=?",(pid,bid),one=True)

    @staticmethod
    def _body_tex(document,rendered=None):
        sections=[]
        for section in document["sections"]:
            blocks=[(rendered or {}).get(block["id"],block["tex"]) for block in section["blocks"]]
            command={1:"section",2:"subsection",3:"subsubsection"}.get(section.get("level",1),"section")
            sections.append("\\"+command+"{"+_escape_plain(section["title"])+"}\n\n"+"\n\n".join(blocks))
        return "\n\n".join(sections)

    def _tex_document(self,document,rendered=None,tracked=False):
        binding=load_binding(self._folder(document["project_id"]))
        if binding:
            return render_source(binding,document,rendered,tracked)
        provenance=sorted({s["source_type"] for s in document["sections"]})
        header=[
            "% Generated editable working copy; not claimed to be the author's original TeX.",
            "% Source types: "+", ".join(p.replace("\n"," ").replace("\r"," ") for p in provenance),
            "% Document revision: "+str(document["revision"]),
            r"\documentclass[11pt]{article}",
            r"\usepackage[margin=1in]{geometry}",
            r"\usepackage{amsmath,amssymb,bm,mathrsfs}",
            r"\usepackage{xcolor,graphicx,hyperref}",
            r"\usepackage{iftex}",
            r"\ifPDFTeX\usepackage[T1]{fontenc}\usepackage[utf8]{inputenc}\else\usepackage{fontspec}\fi",
            r"\begin{document}",r"\color{black}",
        ]
        if tracked: header.extend([
            "% Tracked: black = unchanged; red = insertion; blue = deletion.",
            "% Pending proposals shown here are not accepted manuscript text.",
        ])
        return "\n".join(header)+"\n\n"+self._body_tex(document,rendered)+"\n\n\\end{document}\n"

    def _snapshot(self,db,pid,actor,reason):
        document=self._document(db,pid,versions=False); tex=self._tex_document(document)
        folder=self._folder(pid)/"versions"; folder.mkdir(parents=True,exist_ok=True)
        stem=f"v{document['revision']:06d}_{uid('snapshot')[-8:]}"
        jpath,tpath=folder/(stem+".json"),folder/(stem+".tex")
        with jpath.open("x",encoding="utf-8",newline="\n") as fh: fh.write(json.dumps(document,ensure_ascii=False,indent=2))
        with tpath.open("x",encoding="utf-8",newline="\n") as fh: fh.write(tex)
        db.execute("INSERT INTO manuscript_versions VALUES(?,?,?,?,?,?,?,?)",
                   (pid,document["revision"],jpath.relative_to(self.root).as_posix(),tpath.relative_to(self.root).as_posix(),digest(tex),now(),actor,reason))

    @staticmethod
    def _section_input(section):
        if not isinstance(section,dict) or not all(isinstance(section.get(k),str) for k in ("id","title","role_id","source_tex")):
            raise ValueError("章节需要 id/title/role_id/source_tex")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,80}",section["id"]): raise ValueError("章节 ID 无效")
        level=section.get("level",1); parent=section.get("parent_id")
        if isinstance(level,bool) or not isinstance(level,int) or not 1<=level<=3: raise ValueError("章节层级须为 1–3")
        if parent is not None and (not isinstance(parent,str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,80}",parent)):
            raise ValueError("父章节 ID 无效")
        if parent==section["id"]: raise ValueError("章节不能作为自己的父级")
        validate_tex(section["source_tex"]); return section

    def _insert_section(self,db,pid,section,position):
        db.execute("INSERT INTO manuscript_sections(project_id,id,title,role_id,position,source_type,level,parent_id) VALUES(?,?,?,?,?,?,?,?)",
                   (pid,section["id"],section["title"],section["role_id"],position,str(section.get("source_type") or "user_tex"),
                    section.get("level",1),section.get("parent_id")))
        for pos,block in enumerate(split_tex_blocks(section["source_tex"])):
            db.execute("INSERT INTO manuscript_blocks VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (uid("block"),pid,section["id"],pos,block["kind"],block["tex"],1,digest(block["tex"]),now(),now()))

    def ensure(self,project_id,sections):
        pid=project_id
        self._project(pid)
        if not isinstance(sections,list) or not sections: raise ValueError("初稿至少需要一个章节")
        validated=[self._section_input(s) for s in sections]
        if len({s["id"] for s in validated})!=len(validated): raise ValueError("章节 ID 不得重复")
        seen={}
        for section in validated:
            parent=section.get("parent_id")
            if parent and (parent not in seen or seen[parent].get("level",1)>=section.get("level",1)):
                raise ValueError("父章节须先出现，且层级小于子章节")
            seen[section["id"]]=section
        if sum(len(s["source_tex"]) for s in validated)>5000000: raise ValueError("稿件过长")
        with self.store.lock,self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM manuscript_documents WHERE project_id=?",(pid,)).fetchone():
                stamp=now(); db.execute("INSERT INTO manuscript_documents VALUES(?,?,?,?)",(pid,1,stamp,stamp))
                for pos,section in enumerate(validated): self._insert_section(db,pid,section,pos)
                self._snapshot(db,pid,"user","Initial working copy with explicit provenance")
        return self.get(pid)

    def import_latex(self,project_id,path,original_path=None,assets_path=None):
        """Initialize a NEW work manuscript from local source without model calls.

        The imported author template is immutable. Only selected bodies enter
        the normal validated editor. Export never writes back to either source.
        """
        pid=self._project(project_id)
        source=contained_path(self.root,path)
        original=contained_path(self.root,original_path or path)
        assets=contained_path(self.root,assets_path or str(source.parent),directory=True)
        raw=source.read_bytes(); original_raw=original.read_bytes()
        if len(raw)>5_000_000 or len(original_raw)>5_000_000:
            raise ValueError("LaTeX 来源过大")
        try:
            text=raw.decode("utf-8-sig").replace("\r\n","\n").replace("\r","\n")
        except UnicodeDecodeError as exc:
            raise ValueError("LaTeX 来源需要 UTF-8 编码") from exc
        slots=parse_source(text)
        validated=[self._section_input(slot) for slot in slots]
        folder=self._folder(pid)
        binding_path=folder/"source_binding.json"
        source_hash=hashlib.sha256(raw).hexdigest()
        original_hash=hashlib.sha256(original_raw).hexdigest()
        with self.store.lock,self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM manuscript_documents WHERE project_id=?",(pid,)).fetchone():
                raise ManuscriptConflict("原版 LaTeX 只能导入尚未初始化的新稿件；已有编辑不会被覆盖")
            if binding_path.exists():
                raise ManuscriptConflict("此项目已有原版 LaTeX 绑定记录，未覆盖来源")
            folder.mkdir(parents=True,exist_ok=True)
            archive=folder/"sources"; archive.mkdir(exist_ok=True)
            for name,content in (("original_"+original_hash+".tex",original_raw),
                                 ("imported_"+source_hash+".tex",raw)):
                target=archive/name
                if target.exists():
                    if target.read_bytes()!=content:
                        raise ValueError("已有来源快照指纹不匹配")
                else:
                    with target.open("xb") as fh: fh.write(content)
            stamp=now()
            db.execute("INSERT INTO manuscript_documents VALUES(?,?,?,?)",(pid,1,stamp,stamp))
            for index,section in enumerate(validated):
                self._insert_section(db,pid,section,index)
            document=self._document(db,pid,versions=False)
            metadata=dict(path=source.relative_to(self.root).as_posix(),
                          original_path=original.relative_to(self.root).as_posix(),
                          assets_path=assets.relative_to(self.root).as_posix(),
                          source_sha256=source_hash,original_sha256=original_hash,
                          original_snapshot=(archive/("original_"+original_hash+".tex")).relative_to(self.root).as_posix(),
                          imported_at=stamp)
            binding=build_binding(text,slots,document,metadata)
            binding_created=False
            try:
                with binding_path.open("x",encoding="utf-8",newline="\n") as fh:
                    binding_created=True
                    fh.write(json.dumps(binding,ensure_ascii=False,indent=2))
                self._snapshot(db,pid,"user_authorized_revision","Imported original LaTeX working project; untouched template and sections preserved")
            except Exception:
                # This transaction created the binding exclusively; it is not
                # a user file or a prior import and must not outlive rollback.
                if binding_created:
                    binding_path.unlink(missing_ok=True)
                raise
        settings=self.root/"projects"/pid/"editor_settings.json"
        settings.write_text(json.dumps({"auto_review":False},ensure_ascii=False),encoding="utf-8")
        return self.get(pid)

    def _affected(self,db,pid,block,original,proposed):
        sections=[dict(s) for s in db.execute("SELECT * FROM manuscript_sections WHERE project_id=? ORDER BY position",(pid,))]
        changed=next(s for s in sections if s["id"]==block["section_id"])
        before,after=_references(original),_references(proposed)
        labels=before["labels"]|after["labels"]; cites=before["cites"]^after["cites"]; symbols=before["symbols"]^after["symbols"]
        reasons={block["section_id"]:["edited section and adjacent blocks"]}
        for section in sections:
            if section["role_id"] in _ROLE_CHAIN.get(changed["role_id"],set()):
                reasons.setdefault(section["id"],[]).append("section-role dependency; review candidate, not a forced rewrite")
            if (section["id"]==changed.get("parent_id") or section.get("parent_id")==changed["id"] or
                (changed.get("parent_id") and section.get("parent_id")==changed["parent_id"])):
                reasons.setdefault(section["id"],[]).append("parent/child manuscript context; review candidate")
        for candidate in db.execute("SELECT * FROM manuscript_blocks WHERE project_id=?",(pid,)):
            refs=_references(candidate["tex"]); sid=candidate["section_id"]
            if labels & refs["refs"]: reasons.setdefault(sid,[]).append("references an edited equation/label")
            if cites & refs["cites"]: reasons.setdefault(sid,[]).append("shares a changed citation")
            if symbols & refs["symbols"]: reasons.setdefault(sid,[]).append("shares changed mathematical symbols")
        return [s["id"] for s in sections if s["id"] in reasons],{k:sorted(set(v)) for k,v in reasons.items()}

    def _edit(self,db,pid,bid,tex,base_hash,reason,actor,kind="manual_edit",snapshot=True):
        row=db.execute("SELECT * FROM manuscript_blocks WHERE project_id=? AND id=?",(pid,bid)).fetchone()
        if row is None: raise ValueError("段落不存在")
        block=dict(row)
        if block["sha256"]!=base_hash: raise ManuscriptConflict("段落已变化，请基于当前版本编辑；未覆盖原内容",block)
        revision=db.execute("SELECT revision FROM manuscript_documents WHERE project_id=?",(pid,)).fetchone()[0]
        if tex==block["tex"]:
            return dict(block=block,document_revision=revision,change_id=None,affected_sections=[],dependency_reasons={},unchanged=True)
        affected,reasons=self._affected(db,pid,block,block["tex"],tex)
        revision+=1; cid=uid("change"); stamp=now()
        db.execute("UPDATE manuscript_blocks SET tex=?,sha256=?,kind=?,revision=revision+1,updated_at=? WHERE id=?",
                   (tex,digest(tex),_block_kind(tex),stamp,bid))
        db.execute("UPDATE manuscript_documents SET revision=?,updated_at=? WHERE project_id=?",(revision,stamp,pid))
        db.execute("INSERT INTO manuscript_changes VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (cid,pid,bid,revision,block["tex"],tex,base_hash,digest(tex),actor,reason,kind,stamp))
        db.execute("UPDATE manuscript_suggestions SET status='stale',updated_at=? WHERE project_id=? AND block_id=? AND status='pending' AND base_hash<>?",
                   (stamp,pid,bid,digest(tex)))
        if snapshot: self._snapshot(db,pid,actor,reason)
        updated=dict(db.execute("SELECT * FROM manuscript_blocks WHERE id=?",(bid,)).fetchone())
        return dict(block=updated,document_revision=revision,change_id=cid,affected_sections=affected,dependency_reasons=reasons,unchanged=False)

    def edit_block(self,project_id,block_id,tex,base_hash,reason="",actor="user"):
        pid,bid=project_id,block_id
        self._project(pid); validate_tex(tex)
        with self.store.lock,self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._edit(db,pid,bid,tex,base_hash,str(reason),str(actor))

    def add_suggestion(self,project_id,block_id,proposed_tex,reason,expert,base_hash,kind="prose",severity="major",evidence=None,job_id=""):
        pid=project_id
        self._project(pid); validate_tex(proposed_tex)
        if not isinstance(reason,str) or not reason.strip() or not isinstance(expert,str) or not expert.strip(): raise ValueError("建议需要具体理由和负责专家")
        if severity not in {"critical","major","minor","info"}: raise ValueError("建议严重程度无效")
        if not isinstance(evidence or [],list): raise ValueError("建议依据须为列表")
        with self.store.lock,self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            block=db.execute("SELECT * FROM manuscript_blocks WHERE project_id=? AND id=?",(pid,block_id)).fetchone()
            if block is None: raise ValueError("段落不存在")
            original,status=block["tex"],"pending"
            if block["sha256"]!=base_hash:
                old=db.execute("SELECT * FROM manuscript_changes WHERE project_id=? AND block_id=? AND (before_hash=? OR after_hash=?) ORDER BY document_revision DESC LIMIT 1",
                               (pid,block_id,base_hash,base_hash)).fetchone()
                if old is None: raise ManuscriptConflict("建议依赖的原文版本不存在，请重新读取段落",dict(block))
                original=old["original_tex"] if old["before_hash"]==base_hash else old["proposed_tex"]; status="stale"
            sid=uid("suggestion"); stamp=now()
            db.execute("INSERT INTO manuscript_suggestions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (sid,pid,block_id,original,proposed_tex,reason,expert,base_hash,kind,severity,json.dumps(evidence or [],ensure_ascii=False),job_id,status,stamp,stamp,None,None))
        return self.suggestion(sid)

    def suggestion(self,suggestion_id):
        sid=suggestion_id
        with self.store.connect() as db:
            return self._suggestion(db.execute("SELECT * FROM manuscript_suggestions WHERE id=?",(sid,)).fetchone())

    def accept(self,suggestion_id,proposed_tex=None,actor="user"):
        sid=suggestion_id
        if proposed_tex is not None: validate_tex(proposed_tex)
        conflict=None; result=None
        with self.store.lock,self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT * FROM manuscript_suggestions WHERE id=?",(sid,)).fetchone()
            if row is None: raise ValueError("建议不存在")
            if row["status"] not in {"pending","stale"}: raise ManuscriptConflict("该建议已经处理，不能重复接受")
            block=db.execute("SELECT * FROM manuscript_blocks WHERE project_id=? AND id=?",(row["project_id"],row["block_id"])).fetchone()
            if block is None or block["sha256"]!=row["base_hash"]:
                db.execute("UPDATE manuscript_suggestions SET status='stale',updated_at=? WHERE id=?",(now(),sid))
                conflict=dict(block) if block else {"block_id":row["block_id"],"missing":True}
            else:
                text=row["proposed_tex"] if proposed_tex is None else proposed_tex
                kind="user_edited_proposal" if text!=row["proposed_tex"] else "accepted_proposal"
                result=self._edit(db,row["project_id"],row["block_id"],text,row["base_hash"],row["reason"],str(actor),kind,snapshot=False)
                db.execute("UPDATE manuscript_suggestions SET status='accepted',accepted_tex=?,change_id=?,updated_at=? WHERE id=?",
                           (text,result["change_id"],now(),sid))
                if not result["unchanged"]: self._snapshot(db,row["project_id"],str(actor),row["reason"])
                result.update(suggestion_id=sid,edited_before_accept=kind=="user_edited_proposal")
        if conflict is not None: raise ManuscriptConflict("原段落已变化；建议保留为 stale，未覆盖人工编辑",conflict)
        return result

    def reject(self,suggestion_id):
        sid=suggestion_id
        with self.store.lock,self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT * FROM manuscript_suggestions WHERE id=?",(sid,)).fetchone()
            if row is None: raise ValueError("建议不存在")
            if row["status"]=="accepted": raise ManuscriptConflict("已接受的建议需用新编辑撤回；拒绝不会回滚正文")
            db.execute("UPDATE manuscript_suggestions SET status='rejected',updated_at=? WHERE id=?",(now(),sid))
        return self.suggestion(sid)

    def import_section(self,project_id,section_id,title,role_id,body_tex,source="user",replace=False,base_revision=None,level=1,parent_id=None):
        pid=project_id
        self._project(pid)
        section=self._section_input(dict(id=section_id,title=title,role_id=role_id,source_tex=body_tex,source_type=source,level=level,parent_id=parent_id))
        with self.store.lock,self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            doc=db.execute("SELECT * FROM manuscript_documents WHERE project_id=?",(pid,)).fetchone()
            if doc is None: raise ValueError("请先初始化稿件")
            if parent_id:
                parent=db.execute("SELECT * FROM manuscript_sections WHERE project_id=? AND id=?",(pid,parent_id)).fetchone()
                if not parent or parent["level"]>=level: raise ValueError("父章节不存在或层级不正确")
            if base_revision is not None and doc["revision"]!=base_revision: raise ManuscriptConflict("整节导入的文档版本已变化")
            old=db.execute("SELECT * FROM manuscript_sections WHERE project_id=? AND id=?",(pid,section_id)).fetchone()
            if old and not replace: raise ManuscriptConflict("章节已存在；默认不替换已有人工编辑")
            if old:
                position=old["position"]
                db.execute("UPDATE manuscript_suggestions SET status='stale',updated_at=? WHERE project_id=? AND block_id IN (SELECT id FROM manuscript_blocks WHERE project_id=? AND section_id=?) AND status='pending'",
                           (now(),pid,pid,section_id))
                db.execute("DELETE FROM manuscript_blocks WHERE project_id=? AND section_id=?",(pid,section_id))
                db.execute("DELETE FROM manuscript_sections WHERE project_id=? AND id=?",(pid,section_id))
            else:
                position=db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM manuscript_sections WHERE project_id=?",(pid,)).fetchone()[0]
            self._insert_section(db,pid,section,position)
            db.execute("UPDATE manuscript_documents SET revision=revision+1,updated_at=? WHERE project_id=?",(now(),pid))
            self._snapshot(db,pid,str(source),"Explicit section import: "+section_id)
        return self.get(pid)

    def export_tex(self,project_id,tracked=False):
        pid=project_id
        self._project(pid)
        with self.store.connect() as db:
            db.execute("BEGIN")
            document=self._document(db,pid)
            if document is None: raise ValueError("可编辑稿件尚未初始化")
            rendered={}
            if tracked:
                for section in document["sections"]:
                    for block in section["blocks"]:
                        active=[s for s in document["suggestions"] if s["block_id"]==block["id"] and s["status"]=="pending" and s["base_hash"]==block["sha256"]]
                        if active:
                            parts=active[-1]["diff"]
                        else:
                            change=db.execute("SELECT * FROM manuscript_changes WHERE project_id=? AND block_id=? AND after_hash=? ORDER BY document_revision DESC LIMIT 1",
                                              (pid,block["id"],block["sha256"])).fetchone()
                            if not change: continue
                            parts=semantic_diff(change["original_tex"],change["proposed_tex"],block["kind"]=="math")
                        colors={"equal":"black","insert":"red","delete":"blue"}
                        # Spaces/newlines between groups preserve the original token stream;
                        # whole display environments use color groups, never textcolor boxes.
                        rendered[block["id"]]="".join("{\\color{"+colors[p["kind"]]+"} "+p["text"]+"}" for p in parts)
            tex=self._tex_document(document,rendered,tracked)
        folder=self._folder(pid); folder.mkdir(parents=True,exist_ok=True)
        name="main-tracked.tex" if tracked else "main.tex"
        snapshot_dir=folder/"exports"/f"v{document['revision']:06d}_{digest(tex)[:12]}"
        snapshot_dir.mkdir(parents=True,exist_ok=True); snapshot=snapshot_dir/name
        if snapshot.exists() and snapshot.read_text(encoding="utf-8")!=tex: raise ValueError("已有导出快照内容不匹配")
        if not snapshot.exists():
            with snapshot.open("x",encoding="utf-8",newline="\n") as fh: fh.write(tex)
        target=folder/name; temporary=folder/(uid("export")+".pending")
        temporary.write_text(tex,encoding="utf-8",newline="\n"); temporary.replace(target)
        return dict(tex=tex,path=str(target),snapshot_path=str(snapshot),revision=document["revision"],
                    sha256=digest(tex),tracked=bool(tracked),source_types=sorted({s["source_type"] for s in document["sections"]}))
