"""Loopback HTTP application; no build step, external web UI, or background billing."""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .library import Library
from .library_enrichment import LibraryEnrichment, public_topics
from .providers import CodexProvider, MockProvider
from .store import Store
from .workflow import Workflow
from .manuscript import ManuscriptStore,ManuscriptConflict
from .manuscript_seed import initial_sections,editor_settings
from .editor_agents import EditorAgents
from .source_review import SourceReviewStore
from .translation import TranslationAgents
from .tex_preview import TexPreviewJobs
from .source_agents import SourceWritingAgents
from .discussion_context import read_discussion_context
from .project_upload import import_project,MAX_ZIP


def seed_project(store,library,root):
    if store.query("SELECT id FROM projects LIMIT 1"):
        return
    folder=root/"projects/bioworld_seed"
    if not folder.exists():
        return
    p=store.create_project("BioWorld-MI · 当前改稿","基于已接受的六段 Introduction、状态分解与 Readout-Coupled Forward Generation，继续完善论文。用户已接受的写作方案不等于现有模型代码已实现。","IEEE TMI / MIA（待确定最终目标）")
    sources=[x["id"] for x in library.records() if x.get("kind") in {"manuscript","exemplar","reference","theory","guidance"}]
    store.execute("UPDATE projects SET source_ids=? WHERE id=?",(json.dumps(sources),p["id"]))
    method=next(folder.glob("*Method论文公式版.md"),None)
    intro=folder/"APPROVED_INTRODUCTION.md"
    for rid,path in (("method",method),("introduction",intro)):
        if path and path.exists():
            content=path.read_text(encoding="utf-8-sig")
            payload=dict(summary="用户已接受的写作基线；不表示模型已实现。",content=content,issues=[],citations=[],needs_evidence=[],decision="pass")
            store.artifact(p["id"],"seed",rid,content,payload,{"passed":True,"issues":[],"scope":"用户提供/接受的基线，未重新科学验收。"},status="user_approved_baseline")


class Application:
    def __init__(self,root,probe=True):
        self.root=Path(root).resolve()
        self.store=Store(self.root)
        self.library=Library(self.root)
        self.enrichment=LibraryEnrichment(self.library,self.store)
        self.codex=CodexProvider(self.root)
        self.mock=MockProvider(self.root)
        self.workflow=Workflow(self.root,{"codex":self.codex,"mock":self.mock},self.store)
        self.manuscript=ManuscriptStore(self.root,self.store)
        self.source_review=SourceReviewStore(self.root,self.store,self.manuscript)
        self.translation=TranslationAgents(self.root,{"codex":self.codex},store=self.store)
        self.preview=TexPreviewJobs(self.root,self.source_review,self.store)
        self.source_agents=SourceWritingAgents(self.root,self.source_review,{"codex":self.codex},self.store)
        self.editor=EditorAgents(self.root,self.manuscript,{"codex":self.codex},self.library,self.workflow.revisions,self.store)
        self.provider_status={"available":False,"authenticated":False,"models":[],"error":"正在检查本地 Codex 登录与模型目录。"}
        self.store.recover_interrupted()
        self.editor.recover_interrupted()
        self.translation.recover_interrupted()
        seed_project(self.store,self.library,self.root)
        if probe:
            threading.Thread(target=self.refresh_provider,daemon=True).start()
            threading.Thread(target=self.translation.fast.prewarm,daemon=True).start()

    def refresh_provider(self):
        try:
            self.provider_status=self.codex.status(refresh=True)
        except Exception as exc:
            self.provider_status=dict(available=False,authenticated=False,models=[],error=str(exc))
        return self.provider_status

    def state(self):
        papers=self.library.records()
        return dict(projects=self.store.query("SELECT * FROM projects ORDER BY created_at DESC"),
                    roles=self.workflow.roles(),
                    runs=self.store.query("SELECT * FROM runs ORDER BY created_at DESC LIMIT 30"),
                    provider=self.provider_status,library={"count":len(papers),"papers":papers,"abstract_count":len(self.library.abstract_records()),"tracks":self.library.collection_counts()},
                    skills=[{"id":p.name,"path":str(p),"available":(p/"SKILL.md").exists()} for p in (self.root/"skills").iterdir() if p.is_dir()],
                    experiences=self.store.query("SELECT * FROM experiences ORDER BY created_at DESC LIMIT 50"),
                    policy=self.workflow.config)

    def run_detail(self,rid):
        run=self.store.run(rid)
        if not run:
            return None
        run["events"]=self.store.query("SELECT * FROM events WHERE run_id=? ORDER BY id",(rid,))
        run["artifacts"]=self.store.query("SELECT * FROM artifacts WHERE run_id=? ORDER BY created_at",(rid,))
        run["invocations"]=self.store.query("SELECT * FROM invocations WHERE run_id=? ORDER BY created_at",(rid,))
        run["errors"]=[run["error"]] if run["error"] else []
        return run

    def manuscript_document(self,pid):
        if not self.store.project(pid):
            raise ValueError("课题不存在")
        if self.source_review.active(pid):
            return self.source_review.legacy_document(pid)
        document=self.manuscript.get(pid)
        if document is None:
            document=self.manuscript.ensure(pid,initial_sections(self.root,self.store,pid))
        document.update(editor_settings(self.root,pid))
        document["jobs"]=self.editor.list_jobs(pid)
        if document.get("source_type")!="original_latex_revision":
            document["source_note"]="可编辑工作稿；原PDF与已接受推导保留为对照来源，未声称逐字恢复原始TeX。"
            document["source_pdf_url"]="/api/manuscript/"+pid+"/source.pdf"
        return document

    def sync_change(self,pid,result):
        if result.get("change_id") and not result.get("unchanged"):
            job=self.editor.submit(pid,"sync",message="根据刚刚采纳或手工保存的修改，更新必要的关联文字和引用；不得改写触发段落或发明新实验。",
                auto_apply=True,changed_block_id=result["block"]["id"],change_id=result["change_id"])
            result.update(context_job=job,context_job_id=job["id"])
        return result

    def accept_source(self,pid,proposal_id,data):
        before=self.source_review.get(pid)
        proposal=self.source_review.proposal(pid,proposal_id)
        chosen=[h for h in proposal["hunks"] if h["status"]=="pending" and
                (data.get("selected_hunk_ids") is None or h["id"] in data["selected_hunk_ids"])]
        if data.get("proposed_tex") is not None:
            chosen=[dict(start=proposal["start"],end=proposal["end"],
                         original_tex=before["tex"][proposal["start"]:proposal["end"]],proposed_tex=data["proposed_tex"])]
        result=self.source_review.accept(pid,proposal_id,data["base_hash"],data.get("selected_hunk_ids"),data.get("proposed_tex"))
        if proposal.get("review_mode") not in {"local","layout"} or result["hash"]==before["hash"]:
            return result
        delta=[];shift=0
        for h in sorted(chosen,key=lambda h:h["start"]):
            start=h["start"]+shift
            delta.append(dict(original_tex=h["original_tex"],proposed_tex=h["proposed_tex"],
                              new_start=start,new_end=start+len(h["proposed_tex"])))
            shift+=len(h["proposed_tex"])-(h["end"]-h["start"])
        try:
            job=self.source_agents.submit_linkage(pid,proposal_id,before["hash"],result["hash"],delta)
            if job:
                result.update(linkage_job_id=job["id"],linkage_job=job)
            else:
                result["linkage_notice"]="已采纳关联建议；未再次自动调用，避免循环。"
        except Exception as exc:
            result["linkage_error"]="正文已成功采纳，关联检查未启动："+str(exc)
        return result


class Handler(BaseHTTPRequestHandler):
    server_version="PaperAgent/0.1"
    def log_message(self,fmt,*args):
        # Access logs omit manuscript bodies and model prompts.
        pass

    @property
    def app(self):
        return self.server.application

    def _json(self,data,status=200):
        raw=json.dumps(data,ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(raw)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError):
            pass

    def _host_ok(self):
        return self.headers.get("Host","").lower() in {f"127.0.0.1:{self.server.server_port}",f"localhost:{self.server.server_port}"}

    def _write_ok(self, content_type="application/json"):
        if not self._host_ok():
            raise PermissionError("仅允许本机界面访问")
        origin=self.headers.get("Origin")
        if origin and origin not in {f"http://127.0.0.1:{self.server.server_port}",f"http://localhost:{self.server.server_port}"}:
            raise PermissionError("请求来源不匹配")
        if self.headers.get("Sec-Fetch-Site")=="cross-site":
            raise PermissionError("不接受跨站写入")
        if self.headers.get_content_type()!=content_type:
            raise ValueError("请求内容类型不匹配")

    def do_GET(self):
        if not self._host_ok():
            return self._json({"error":"仅允许本机访问"},403)
        try:
            parts=urllib.parse.urlsplit(self.path)
            path=parts.path
            if path=="/api/health":
                return self._json({"ok":True,"version":"0.1.0","scope":"writing-only","root":str(self.app.root),"pid":os.getpid()})
            if path=="/api/state":
                return self._json(self.app.state())
            if path.startswith("/api/source/"):
                segments=path.strip("/").split("/")
                if len(segments)<3:
                    raise ValueError("稿件路径无效")
                pid=segments[2]
                query=urllib.parse.parse_qs(parts.query)
                if len(segments)==3:
                    return self._json(self.app.source_review.get(pid))
                if len(segments)==4 and segments[3]=="discussion-context":
                    if not self.app.store.project(pid):
                        return self._json({"error":"课题不存在"},404)
                    return self._json(read_discussion_context(self.app.root,pid) or
                                      {"project_id":pid,"sections":[],"messages":[],"summary":"本课题尚未导入讨论。"})
                if len(segments)==4 and segments[3]=="export":
                    return self._json(self.app.source_review.export(pid))
                if len(segments)==5 and segments[3]=="proposal":
                    return self._json(self.app.source_review.proposal(pid,segments[4]))
                if len(segments)==4 and segments[3]=="file":
                    return self._json(self.app.source_review.file(pid,query.get("name",["main.tex"])[0]))
                if len(segments)==4 and segments[3]=="asset":
                    file=self.app.source_review.asset(pid,query.get("name",[""])[0])
                    raw=file.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type",mimetypes.guess_type(file.name)[0] or "application/octet-stream")
                    self.send_header("Content-Length",str(len(raw)))
                    self.send_header("Cache-Control","no-store")
                    self.send_header("X-Content-Type-Options","nosniff")
                    self.send_header("Content-Security-Policy","default-src 'none'; sandbox")
                    self.end_headers();self.wfile.write(raw)
                    return
                return self._json({"error":"稿件审阅接口不存在"},404)
            if path.startswith("/api/translation/jobs/"):
                job=self.app.translation.get_job(path.split("/")[-1])
                return self._json(job if job else {"error":"翻译任务不存在"},200 if job else 404)
            if path.startswith("/api/source-agent/jobs/"):
                job=self.app.source_agents.get(path.split("/")[-1])
                return self._json(job if job else {"error":"专家任务不存在"},200 if job else 404)
            if path=="/api/source-agent/jobs":
                query=urllib.parse.parse_qs(parts.query)
                pid=query.get("project_id",[""])[0]
                return self._json({"jobs":self.app.source_agents.list_jobs(pid,query.get("conversation_id",[None])[0])})
            if path.startswith("/api/preview/"):
                segments=path.strip("/").split("/")
                pid=segments[2]
                if len(segments)==4 and segments[3]=="latest":
                    return self._json(self.app.preview.latest(pid))
                if len(segments)==5 and segments[3]=="jobs":
                    return self._json(self.app.preview.get(pid,segments[4]))
                if len(segments)==5 and segments[3]=="pdf":
                    file=self.app.preview.pdf_path(pid,segments[4]);raw=file.read_bytes()
                    self.send_response(200);self.send_header("Content-Type","application/pdf")
                    self.send_header("Content-Length",str(len(raw)));self.send_header("Cache-Control","no-store")
                    self.end_headers();self.wfile.write(raw)
                    return
                return self._json({"error":"编译任务不存在"},404)
            if path.startswith("/api/manuscript/"):
                segments=path.strip("/").split("/")
                pid=segments[2]
                if len(segments)==3:
                    return self._json(self.app.manuscript_document(pid))
                if len(segments)==4 and segments[3]=="export":
                    if self.app.source_review.active(pid):
                        return self._json(self.app.source_review.export(pid))
                    self.app.manuscript_document(pid)
                    tracked=urllib.parse.parse_qs(parts.query).get("tracked",["0"])[0] in {"1","true"}
                    return self._json(self.app.manuscript.export_tex(pid,tracked))
                if len(segments)==4 and segments[3]=="source.pdf":
                    plan=self.app.workflow.revisions.load(pid)
                    paper=next((p for p in self.app.library.records() if plan and p["id"]==plan["manuscript_id"]),None)
                    if not paper:
                        return self._json({"error":"本课题未绑定原PDF"},404)
                    raw=self.app.library.resolved(paper["path"]).read_bytes()
                    self.send_response(200);self.send_header("Content-Type","application/pdf")
                    self.send_header("Content-Length",str(len(raw)));self.end_headers();self.wfile.write(raw)
                    return
                return self._json({"error":"稿件接口不存在"},404)
            if path.startswith("/api/suggestions/"):
                suggestion=self.app.manuscript.suggestion(path.split("/")[-1])
                return self._json(suggestion if suggestion else {"error":"建议不存在"},200 if suggestion else 404)
            if path.startswith("/api/editor/jobs/"):
                job=self.app.editor.get_job(path.split("/")[-1])
                return self._json(job if job else {"error":"编辑任务不存在"},200 if job else 404)
            if path.startswith("/api/projects/"):
                pid=path.split("/")[-1]
                project=self.app.store.project(pid)
                if not project:
                    return self._json({"error":"项目不存在"},404)
                source=self.app.store.query("SELECT sha256 FROM source_documents WHERE project_id=?",(pid,),one=True)
                return self._json(dict(project=project,source_hash=source["sha256"] if source else "",source_ids=project["source_ids"],revision_plan=self.app.workflow.revisions.load(pid),
                     artifacts=self.app.store.query("SELECT * FROM artifacts WHERE project_id=? ORDER BY created_at DESC",(pid,)),
                     messages=self.app.store.query("SELECT * FROM messages WHERE project_id=? ORDER BY created_at",(pid,)),
                     runs=self.app.store.query("SELECT * FROM runs WHERE project_id=? ORDER BY created_at DESC",(pid,))))
            if path.startswith("/api/runs/"):
                result=self.app.run_detail(path.split("/")[-1])
                return self._json(result if result else {"error":"任务不存在"},200 if result else 404)
            if path.startswith("/api/artifacts/"):
                result=self.app.store.query("SELECT * FROM artifacts WHERE id=?",(path.split("/")[-1],),one=True)
                return self._json(result if result else {"error":"版本不存在"},200 if result else 404)
            if path=="/api/library":
                params=urllib.parse.parse_qs(parts.query)
                q=params.get("q",[""])[0]
                track=params.get("track",[None])[0]
                pid=params.get("project_id",[""])[0]
                project=self.app.store.project(pid) if pid else None
                if not q and project:
                    source=self.app.store.query("SELECT tex FROM source_documents WHERE project_id=?",(pid,),one=True)
                    q=" ".join(public_topics(source["tex"] if source else "")) or project["name"]+" "+project["task"]
                if track not in {None,"all","writing","domain","project"}:
                    raise ValueError("用途分类无效")
                papers=self.app.library.search(q,track=track)
                return self._json({"papers":papers,"count":len(papers),"tracks":self.app.library.collection_counts()})
            if path.startswith("/api/figure-files/"):
                parts=path.split("/")
                if len(parts)!=5:
                    return self._json({"error":"图稿不存在"},404)
                aid,name=parts[-2],urllib.parse.unquote(parts[-1])
                artifact=self.app.store.query("SELECT * FROM artifacts WHERE id=?",(aid,),one=True)
                if not artifact or name!=Path(name).name or Path(name).suffix.lower() not in {".svg",".png",".drawio",".pdf",".json"}:
                    return self._json({"error":"图稿不存在"},404)
                folder=(self.app.root/"projects"/artifact["project_id"]/"figures"/aid).resolve()
                file=(folder/name).resolve()
                if not file.is_relative_to(folder) or not file.is_file():
                    return self._json({"error":"图稿不存在"},404)
                raw=file.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type",mimetypes.guess_type(name)[0] or "application/octet-stream")
                self.send_header("Content-Length",str(len(raw)))
                self.send_header("X-Content-Type-Options","nosniff")
                self.send_header("Content-Security-Policy","default-src 'none'; style-src 'unsafe-inline'; sandbox")
                self.end_headers();self.wfile.write(raw)
                return
            if path.startswith("/api/"):
                return self._json({"error":"接口不存在"},404)
            rel="index.html" if path=="/" else urllib.parse.unquote(path).lstrip("/")
            if rel.startswith("static/"):
                rel=rel[7:]
            static=(self.app.root/"static").resolve()
            file=(static/rel).resolve()
            if not file.is_relative_to(static) or not file.is_file():
                return self._json({"error":"页面不存在"},404)
            raw=file.read_bytes()
            self.send_response(200)
            mime=mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            self.send_header("Content-Type",mime+"; charset=utf-8")
            self.send_header("Content-Length",str(len(raw)))
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' blob: data:; worker-src 'self' blob:; frame-src 'self' blob:; object-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(raw)
        except ManuscriptConflict as exc:
            self._json({"error":str(exc),"current":exc.current},409)
        except (ValueError,KeyError,TypeError) as exc:
            self._json({"error":str(exc)},400)
        except Exception as exc:
            self._json({"error":str(exc)},500)

    def do_POST(self):
        try:
            parts=urllib.parse.urlsplit(self.path)
            if parts.path=="/api/projects/upload-zip":
                self._write_ok("application/zip")
                size=int(self.headers.get("Content-Length","0"))
                if not 0<size<=MAX_ZIP:raise ValueError("ZIP 最大支持 64 MB")
                raw=self.rfile.read(size)
                if len(raw)!=size:raise ValueError("ZIP 上传不完整")
                params=urllib.parse.parse_qs(parts.query)
                result=import_project(self.app.root,self.app.store,self.app.source_review,raw,params.get('name',['导入的论文.zip'])[0],params.get('entry',[None])[0])
                if result.get('project'):
                    try:result['preview_job']=self.app.preview.submit(result['project']['id'],result['source']['hash'])
                    except Exception as exc:result['preview_error']=str(exc)
                return self._json(result,200 if result.get('requires_entry') else 201)
            self._write_ok()
            size=int(self.headers.get("Content-Length","0"))
            if not 0<size<=2*1024*1024:
                raise ValueError("请求内容为空或过大")
            data=json.loads(self.rfile.read(size).decode("utf-8"))
            if not isinstance(data,dict):
                raise ValueError("请求必须为 JSON 对象")
            path=urllib.parse.urlsplit(self.path).path
            if path=="/api/library/enrich":
                return self._json(self.app.enrichment.start(str(data["project_id"])),202)
            if path.startswith("/api/source/"):
                segments=path.strip("/").split("/")
                if len(segments)<4:
                    raise ValueError("稿件审阅路径无效")
                pid=segments[2]
                if len(segments)==4 and segments[3]=="prepare-review":
                    return self._json(self.app.source_review.prepare_review(pid,str(data["original_path"]),
                        str(data["candidate_path"]),data.get("assets_path"),data.get("expected_hash")),201)
                if len(segments)==4 and segments[3]=="save":
                    return self._json(self.app.source_review.save(pid,data["tex"],data["base_hash"],data.get("reason","人工编辑")))
                if len(segments)==4 and segments[3]=="restore":
                    return self._json(self.app.source_review.restore_revision(pid,data["revision"],data["base_hash"]))
                if len(segments)==4 and segments[3]=="propose":
                    return self._json(self.app.source_review.propose(pid,data["proposed_tex"],data["reason"],
                        data["base_hash"],data.get("scope","document")),201)
                if len(segments)==4 and segments[3]=="local-edits":
                    if set(data)-{"base_hash","issues","supersede_proposal_ids"}:
                        raise ValueError("局部问题请求包含未知字段")
                    return self._json(self.app.source_review.local_edits(pid,data["issues"],data["base_hash"],
                        data.get("supersede_proposal_ids")),201)
                if len(segments)==4 and segments[3]=="preview":
                    return self._json(self.app.source_review.preview(pid,data["proposal_id"],
                        data.get("selected_hunk_ids"),data.get("proposed_tex")))
                if len(segments)==6 and segments[3]=="proposal":
                    if segments[5]=="accept":
                        return self._json(self.app.accept_source(pid,segments[4],data))
                    if segments[5]=="reject":
                        return self._json(self.app.source_review.reject(pid,segments[4],data.get("selected_hunk_ids")))
                return self._json({"error":"稿件审阅接口不存在"},404)
            if path=="/api/translation/jobs":
                if set(data)-{"project_id","text","context","target_language","source_language","engine","draft_translation"}:
                    raise ValueError("翻译请求包含未知字段")
                return self._json(self.app.translation.submit(str(data["project_id"]),data["text"],
                    context=data.get("context",""),target_language=data.get("target_language","zh-CN"),
                    source_language=data.get("source_language","auto"),engine=data.get("engine","auto"),
                    draft_translation=data.get("draft_translation","")),202)
            if path=="/api/source-agent/jobs":
                if set(data)-{"project_id","message","scope","role_id","action","selection","conversation_id","discussion_section_id","base_hash"}:
                    raise ValueError("专家请求包含未知字段")
                return self._json(self.app.source_agents.submit(str(data["project_id"]),data["message"],
                    scope=data.get("scope","document"),role_id=data.get("role_id"),
                    action=data.get("action","revise"),selection=data.get("selection"),
                    conversation_id=data.get("conversation_id"),discussion_section_id=data.get("discussion_section_id"),expected_hash=data.get("base_hash")),202)
            if path.startswith("/api/source-agent/jobs/") and path.endswith("/cancel"):
                return self._json(self.app.source_agents.cancel(path.split("/")[-2]))
            if path.startswith("/api/translation/jobs/") and path.endswith("/cancel"):
                return self._json(self.app.translation.cancel(path.split("/")[-2]))
            if path.startswith("/api/translation/jobs/") and path.endswith("/feedback"):
                return self._json(self.app.translation.feedback(path.split("/")[-2],data["rating"]))
            if path.startswith("/api/preview/"):
                segments=path.strip("/").split("/")
                if len(segments)==4 and segments[3]=="compile":
                    return self._json(self.app.preview.submit(segments[2],data.get("base_hash")),202)
                if len(segments)==4 and segments[3]=="candidate":
                    if set(data)-{"proposal_id","base_hash","selected_hunk_ids"}:
                        raise ValueError("候选预览请求包含未知字段")
                    return self._json(self.app.preview.submit_candidate(segments[2],data["proposal_id"],
                        data["base_hash"],data.get("selected_hunk_ids")),202)
                return self._json({"error":"编译接口不存在"},404)
            if path.startswith("/api/manuscript/") and path.endswith("/import-latex"):
                segments=path.strip("/").split("/")
                if len(segments)!=4:
                    raise ValueError("原版 LaTeX 导入路径无效")
                pid=segments[2]
                if self.app.source_review.active(pid):
                    raise ManuscriptConflict("已启用原稿审阅；重新导入不会覆盖作者当前稿件")
                self.app.manuscript.import_latex(pid,str(data["path"]),data.get("original_path"),data.get("assets_path"))
                return self._json(self.app.manuscript_document(pid),201)
            if path.startswith("/api/manuscript/") and "/blocks/" in path:
                segments=path.strip("/").split("/")
                if len(segments)!=5:
                    raise ValueError("段落路径无效")
                pid,bid=segments[2],segments[4]
                if self.app.source_review.active(pid):
                    raise ManuscriptConflict("该项目已使用原稿审阅模式，请在新编辑器修改；旧稿件历史保留")
                result=self.app.manuscript.edit_block(pid,bid,str(data["tex"]),str(data["base_hash"]),str(data.get("reason","manual edit")),actor="user")
                return self._json(self.app.sync_change(pid,result))
            if path.startswith("/api/suggestions/") and path.endswith("/accept"):
                sid=path.split("/")[-2]
                suggestion=self.app.manuscript.suggestion(sid)
                if not suggestion:
                    raise ValueError("建议不存在")
                if self.app.source_review.active(suggestion["project_id"]):
                    raise ManuscriptConflict("旧版建议仅保留为历史，请在原稿审阅面板处理当前建议")
                result=self.app.manuscript.accept(sid,proposed_tex=data.get("proposed_tex"),actor="user")
                return self._json(self.app.sync_change(suggestion["project_id"],result))
            if path.startswith("/api/suggestions/") and path.endswith("/reject"):
                return self._json(self.app.manuscript.reject(path.split("/")[-2]))
            if path=="/api/editor/jobs":
                pid=str(data["project_id"])
                if self.app.source_review.active(pid):
                    raise ManuscriptConflict("原稿审阅模式的 Agent 必须读取当前源码；请使用源稿件 Agent 入口")
                self.app.manuscript_document(pid)
                result=self.app.editor.submit(pid,str(data.get("action","review")),str(data.get("message","")),
                    section_id=data.get("section_id"),block_id=data.get("block_id"),auto_apply=data.get("auto_apply"),
                    changed_block_id=data.get("changed_block_id"),change_id=data.get("change_id"))
                return self._json(result,202)
            if path.startswith("/api/editor/jobs/") and path.endswith("/cancel"):
                return self._json(self.app.editor.cancel(path.split("/")[-2]))
            if path=="/api/projects":
                name=data.get("name","").strip()
                if not name or len(name)>200:
                    raise ValueError("项目名称须为 1–200 字符")
                return self._json(self.app.store.create_project(name,str(data.get("task","")),str(data.get("target_journal",""))),201)
            if path=="/api/runs":
                result=self.app.workflow.submit(str(data["project_id"]),data.get("mode","full"),str(data.get("request","")),
                     data.get("role_id","global_teacher"),data.get("provider","codex"),data.get("rounds",1))
                return self._json(result,202)
            if path=="/api/chat":
                result=self.app.workflow.submit(str(data["project_id"]),"discuss",str(data.get("message","")),str(data["role_id"]),data.get("provider","codex"),1)
                return self._json(result,202)
            if path.startswith("/api/runs/") and path.endswith("/cancel"):
                return self._json(self.app.workflow.cancel(path.split("/")[-2]))
            if path.startswith("/api/roles/"):
                return self._json(self.app.workflow.update_role(path.split("/")[-1],str(data["model"]),str(data.get("effort","high"))))
            if path.startswith("/api/projects/") and path.endswith("/sources"):
                pid=path.split("/")[-2]
                if not self.app.store.project(pid):
                    raise ValueError("项目不存在")
                if self.app.store.query("SELECT id FROM runs WHERE project_id=? AND status IN ('queued','running','cancelling')",(pid,)):
                    raise ValueError("项目运行中，来源选择已固定，请在本轮结束后调整")
                ids=data.get("source_ids")
                known={p["id"] for p in self.app.library.all_records()}
                if not isinstance(ids,list) or not all(isinstance(x,str) and x in known for x in ids):
                    raise ValueError("来源编号无效")
                self.app.store.execute("UPDATE projects SET source_ids=? WHERE id=?",(json.dumps(ids),pid))
                return self._json(self.app.store.project(pid))
            if path.startswith("/api/projects/") and path.endswith("/revision-plan"):
                pid=path.split("/")[-2]
                if not self.app.store.project(pid):
                    raise ValueError("项目不存在")
                if self.app.store.query("SELECT id FROM runs WHERE project_id=? AND status IN ('queued','running','cancelling')",(pid,)):
                    raise ValueError("项目运行中，不能更换本轮输入")
                return self._json(self.app.workflow.revisions.bind(pid,str(data["manuscript_id"]),data["guide_ids"],data.get("target_sections"),str(data.get("instructions","")),
                    context_budget_chars=data.get("context_budget_chars",70000)))
            if path=="/api/library/import":
                result=self.app.library.import_file(str(data["path"]),data.get("kind","reference"))
                return self._json(result,201)
            if path=="/api/library/url":
                return self._json(self.app.library.import_url(str(data["url"])),201)
            if path=="/api/provider/refresh":
                return self._json(self.app.refresh_provider())
            return self._json({"error":"接口不存在"},404)
        except PermissionError as exc:
            self._json({"error":str(exc)},403)
        except ManuscriptConflict as exc:
            self._json({"error":str(exc),"current":exc.current},409)
        except (ValueError,KeyError,TypeError) as exc:
            self._json({"error":str(exc)},400)
        except Exception as exc:
            self._json({"error":str(exc)},500)


def main():
    parser=argparse.ArgumentParser(description="本地论文写作 RSI 工作台")
    parser.add_argument("--port",type=int,default=8766)
    parser.add_argument("--root",type=Path,default=Path(__file__).resolve().parents[1])
    args=parser.parse_args()
    root=args.root.resolve()
    (root/"runtime").mkdir(exist_ok=True,parents=True)
    # Lock before recovering stale jobs; two boards must not recover one another.
    lock=(root/"runtime/server.lock").open("a+b")
    if os.name=="nt":
        import msvcrt
        lock.seek(0);lock.write(b"0");lock.flush();lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            raise SystemExit("Paper Agent 已运行。")
    httpd=ThreadingHTTPServer(("127.0.0.1",args.port),Handler)
    httpd.daemon_threads=True
    httpd.application=Application(root)
    (root/"runtime/server.json").write_text(json.dumps({"pid":os.getpid(),"port":args.port,"url":f"http://127.0.0.1:{args.port}","root":str(root)},ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"Paper Agent: http://127.0.0.1:{args.port}",flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for event in list(httpd.application.workflow.cancellations.values()):
            event.set()
        httpd.application.workflow.pool.shutdown(wait=True,cancel_futures=True)
        httpd.application.editor.shutdown(wait=True)
        httpd.application.translation.shutdown(wait=True)
        httpd.application.preview.shutdown(wait=False)
        httpd.application.source_agents.shutdown(wait=False)
        httpd.server_close()
        lock.close()


if __name__=="__main__":
    main()
