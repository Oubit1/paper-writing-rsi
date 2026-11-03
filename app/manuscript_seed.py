"""Build an explicitly attributed editable working copy from available drafts."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .manuscript import markdown_to_tex


def _intro_body(text):
    match=re.search(r"(?m)^Contrast-free myocardial",text)
    if match:
        pieces=re.split(r"\n\s*\n",text[match.start():])
        # The accepted author task has six prose paragraphs; revision notes and
        # comparison tables are preserved in the artifact, not inserted in the paper.
        selected=[]
        for piece in pieces:
            if piece.startswith(("#","|")):
                break
            if piece.strip():
                selected.append(piece.strip())
            if len(selected)==6:
                break
        return "\n\n".join(selected)
    return text


def _method_sections(text):
    pattern=re.compile(r"(?m)^#{1,3}\s*3\.([1-5])\s+([^\n]+)\n")
    matches=list(pattern.finditer(text))
    if len(matches)!=5:
        return []
    result=[]
    for i,match in enumerate(matches):
        body=text[match.end():matches[i+1].start() if i+1<len(matches) else len(text)]
        # Post-method implementation checklists belong to review notes.
        if i==4:
            body=re.split(r"(?m)^##\s+(?!3\.)",body,maxsplit=1)[0]
        result.append(dict(id="method-3-"+match.group(1),title=match.group(2).strip(),role_id="method",
                           source_tex=markdown_to_tex(body.strip()),level=2,parent_id="method",source_type="agent_draft"))
    return result


def _pdf_body(text,start,end):
    first=re.search(start,text)
    if not first:
        return ""
    tail=text[first.end():]
    last=re.search(end,tail) if end else None
    tail=tail[:last.start()] if last else tail
    tail=re.sub(r"(?m)^===== PDF PAGE[^\n]*\n?","",tail)
    tail=re.sub(r"(?m)^(?:IEEE\s*TRANSACTIONS|WANG.*BioWorld|[0-9]+\s*$)[^\n]*\n?","",tail)
    tail=re.sub(r"([a-z])-\n([a-z])",r"\1\2",tail)
    # Source is column-aware PDF text, not a recovered original TeX file.
    paragraphs=re.split(r"\n\s*\n",tail.strip())
    return "\n\n".join(" ".join(p.splitlines()) for p in paragraphs if p.strip())


def initial_sections(root,store,project_id):
    root=Path(root)
    latest=store.latest(project_id)
    sections=[]
    intro=latest.get("introduction")
    if intro:
        sections.append(dict(id="introduction",title="Introduction",role_id="introduction",
             source_tex=markdown_to_tex(_intro_body(intro["content"])),source_type="agent_draft" if intro["run_id"]!="seed" else "author_approved_outline"))
    else:
        sections.append(dict(id="introduction",title="Introduction",role_id="introduction",
             source_tex="% Introduction draft: type or ask the section expert to begin.",source_type="new_working_copy"))
    sections.append(dict(id="related-work",title="Related Works",role_id="related_work",source_tex="% Related work is deferred until the central formulation is stable.",source_type="planned_section"))
    sections.append(dict(id="method",title="Method",role_id="method",source_tex="",source_type="working_copy_heading"))
    method=latest.get("method")
    children=_method_sections(method["content"]) if method else []
    if children:
        sections.extend(children)
    elif method:
        sections.append(dict(id="method-body",title="Formulation and derivation",role_id="method",level=2,parent_id="method",
             source_tex=markdown_to_tex(method["content"]),source_type="author_approved_derivation"))
    else:
        sections.append(dict(id="method-body",title="Formulation and derivation",role_id="method",level=2,parent_id="method",source_tex="% Method draft.",source_type="new_working_copy"))
    source=root/"projects"/project_id/"inputs/manuscript_columns.txt"
    original=source.read_text(encoding="utf-8") if source.exists() else ""
    experiments=_pdf_body(original,r"III\.\s*EXPERIMENTS\s+AND\s+RESULTS",r"IV\.\s*CONCLUSION")
    conclusion=_pdf_body(original,r"IV\.\s*CONCLUSION",r"\nREFERENCES")
    sections.append(dict(id="experiments",title="Experiments and Results",role_id="experiments",
        source_tex=markdown_to_tex(experiments) if experiments else "% Existing experimental results are not replaced by proposed model claims.",source_type="pdf_text_reconstruction"))
    sections.append(dict(id="conclusion",title="Conclusion",role_id="introduction",
        source_tex=markdown_to_tex(conclusion) if conclusion else "% Conclusion to be aligned after evidence is available.",source_type="pdf_text_reconstruction"))
    return sections


def editor_settings(root,project_id):
    path=Path(root)/"projects"/project_id/"editor_settings.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8-sig"))
    # Deployment tests do not start GPT jobs merely by reading a manuscript.
    return {"auto_review":False}
