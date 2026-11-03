"""Managed copies with provenance. Sources are evidence, never executable instructions."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import threading
import urllib.parse
import urllib.request
from pathlib import Path

from .store import now


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def validate_url(url):
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or not u.hostname or u.username or u.password or u.port not in (None,443):
        raise ValueError("仅接收公开 HTTPS 来源。")
    for entry in socket.getaddrinfo(u.hostname,443,type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(entry[4][0]).is_global:
            raise ValueError("不读取本地或私有网络来源。")
    return url


class Library:
    def __init__(self,root):
        self.root = Path(root).resolve()
        self.base = self.root / "library"
        self.base.mkdir(parents=True,exist_ok=True)
        self.index_path = self.base / "index.json"
        self.lock = threading.RLock()
        self._verified_cache={}

    def records(self):
        if not self.index_path.exists():
            return []
        return json.loads(self.index_path.read_text(encoding="utf-8-sig")).get("papers",[])

    def abstract_records(self):
        path=self.base/"abstract_index.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8-sig")).get("papers",[])
        return []

    def all_records(self):
        return self.records()+self.abstract_records()

    @staticmethod
    def tracks(record):
        if record.get("tracks"):
            return record["tracks"]
        if record.get("track"):
            return [record["track"]]
        if record.get("kind") in {"exemplar","abstract_card"}:
            return ["writing"]
        if record.get("kind") in {"manuscript","guidance"}:
            return ["project"]
        return ["domain"]

    def collection_counts(self):
        papers=self.records()
        return {track:sum(track in self.tracks(p) for p in papers) for track in ("writing","domain","project")}

    def merge_collection(self,index_path):
        source=self.resolved(index_path)
        collection=json.loads(source.read_text(encoding="utf-8-sig"))
        incoming=collection.get("papers",[])
        # Validate the entire batch before changing the live index.
        if not incoming or any(not self.verify_record(p) for p in incoming):
            raise ValueError("知识库分册为空或有来源/文本哈希错误")
        with self.lock:
            data=json.loads(self.index_path.read_text(encoding="utf-8-sig")) if self.index_path.exists() else {"schema_version":1,"papers":[]}
            papers=data["papers"]
            for record in incoming:
                old=next((p for p in papers if p["id"]==record["id"] or p["sha256"]==record["sha256"]
                          or (record.get("doi") and str(p.get("doi","")).lower()==str(record["doi"]).lower())),None)
                if old:
                    old["tracks"]=sorted(set(self.tracks(old)+self.tracks(record)))
                    old["source_aliases"]=sorted(set(old.get("source_aliases",[])+[record["id"]]))
                    old["url_aliases"]=sorted(set(old.get("url_aliases",[])+[record["url"]] if record.get("url") else old.get("url_aliases",[])))
                    old.setdefault("collection_records",[])
                    if record["id"] not in {r["id"] for r in old["collection_records"]}:
                        old["collection_records"].append(record)
                else:
                    papers.append(record)
            data.setdefault("collections",{})[source.parent.name]=dict(index_path=source.relative_to(self.root).as_posix(),count=len(incoming),merged_at=now())
            data["updated_at"]=now()
            tmp=self.index_path.with_suffix(".pending")
            tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
            tmp.replace(self.index_path)
        return {"collection":source.parent.name,"records":len(incoming),"counts":self.collection_counts()}

    def resolved(self,path):
        p = Path(path)
        if not p.is_absolute():
            p = self.root / p
        p = p.resolve()
        # Managed library/seed only, never interpret model-supplied paths directly.
        if not p.is_relative_to(self.root):
            raise ValueError("来源记录超出项目目录")
        return p

    def _save(self,record):
        with self.lock:
            papers = self.records()
            old = next((p for p in papers if p.get("sha256")==record["sha256"] and p.get("kind")==record["kind"]),None)
            if old:
                aliases=set(old.get("url_aliases",[]))
                for value in (old.get("url"),record.get("url")):
                    if value:
                        aliases.add(value)
                old["url_aliases"]=sorted(aliases)
                if record.get("url"):
                    old.setdefault("url",record["url"])
                    old["retrieved_at"]=record.get("retrieved_at",now())
                record=old
            else:
                papers.append(record)
            data=json.loads(self.index_path.read_text(encoding="utf-8-sig")) if self.index_path.exists() else {"schema_version":1}
            data.update(updated_at=now(),papers=papers)
            temp = self.index_path.with_suffix(".pending")
            temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
            temp.replace(self.index_path)
            return record

    def import_file(self,path,kind="reference",title=None,url=None):
        if kind not in {"reference","exemplar","manuscript","guidance","theory"}:
            raise ValueError("来源类型无效")
        source = Path(path).expanduser().resolve(strict=True)
        if not source.is_file() or source.suffix.lower() not in {".pdf",".txt",".md",".bib",".html"}:
            raise ValueError("支持 PDF、TXT、Markdown、BibTeX 或 HTML 文本")
        if source.stat().st_size > 100*1024*1024:
            raise ValueError("单份来源不得超过 100 MB")
        data = source.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        sid = "src_" + sha[:16]
        target = self.base / "papers" / (sid + source.suffix.lower())
        target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists():
            target.write_bytes(data)
        if hashlib.sha256(target.read_bytes()).hexdigest()!=sha:
            raise ValueError("来源副本校验失败")
        text = ""
        error = ""
        if source.suffix.lower()==".pdf":
            try:
                import pypdf
                pdf = pypdf.PdfReader(target)
                text = "\n\n".join(f"[PDF page {i+1}]\n{p.extract_text() or ''}" for i,p in enumerate(pdf.pages))
                if not title:
                    title = str((pdf.metadata or {}).get("/Title") or source.stem)
            except Exception as exc:
                error = "PDF 文本提取失败；请人工核对扫描页。" + type(exc).__name__
        else:
            text = data.decode("utf-8-sig",errors="replace")
        txt = self.base / "texts" / (sid+".txt")
        txt.parent.mkdir(parents=True,exist_ok=True)
        txt.write_text(text,encoding="utf-8")
        record = dict(id=sid,title=title or source.stem,kind=kind,
                      path=target.relative_to(self.root).as_posix(),
                      text_path=txt.relative_to(self.root).as_posix(),original_path=str(source),
                      sha256=sha,text_sha256=hashlib.sha256(txt.read_bytes()).hexdigest(),imported_at=now(),text_error=error)
        if url:
            record.update(url=url,retrieved_at=now())
        return self._save(record)

    def import_url(self,url):
        validate_url(url)
        old = next((p for p in self.records() if p.get("url")==url or url in p.get("url_aliases",[])),None)
        if old:
            return old
        req = urllib.request.Request(url,headers={"User-Agent":"PaperAgent/0.1 (source-verification)","Accept":"text/html,text/plain,application/pdf"})
        opener = urllib.request.build_opener(PublicRedirect())
        with opener.open(req,timeout=12) as response:
            validate_url(response.url)
            mime = response.headers.get_content_type()
            if mime not in {"text/html","text/plain","application/pdf","application/xhtml+xml"}:
                raise ValueError("来源格式不支持")
            raw = response.read(5*1024*1024+1)
        if len(raw)>5*1024*1024:
            raise ValueError("远程来源超过 5 MB；可从本地导入")
        path = self.base / "downloads" / (hashlib.sha256(url.encode()).hexdigest()[:20]+(".pdf" if mime=="application/pdf" else ".html"))
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(raw)
        record = self.import_file(path,title=urllib.parse.urlsplit(url).hostname,url=url)
        return record

    def known_ids(self):
        result = set()
        for p in self.all_records():
            if not self.verify_record(p):
                continue
            result.add(p["id"])
            result.update(p.get("source_aliases",[]))
            if p.get("url"):
                result.add(p["url"])
            result.update(p.get("url_aliases",[]))
        return result

    def verify_record(self,record):
        try:
            for field,hash_field in (("path","sha256"),("text_path","text_sha256")):
                path=self.resolved(record[field])
                expected=record.get(hash_field)
                if not expected:
                    return False
                stat=path.stat()
                cache_key=(str(path),stat.st_mtime_ns,stat.st_size,expected)
                if cache_key not in self._verified_cache:
                    self._verified_cache[cache_key]=hashlib.sha256(path.read_bytes()).hexdigest()==expected
                if not self._verified_cache[cache_key]:
                    return False
            return True
        except (OSError,KeyError,ValueError):
            return False

    def search(self,q="",track=None):
        papers = self.all_records() if q.strip() else self.records()
        if track and track!="all":
            papers=[p for p in papers if track in self.tracks(p)]
        words = re.findall(r"[a-zA-Z]{3,}|[\u4e00-\u9fff]{2,}",q.lower())
        if not words:
            return papers
        hits = []
        for p in papers:
            text = (p.get("title","")+" "+p.get("kind","")+" "+json.dumps({k:p.get(k,"") for k in ("domain_topics","writing_use","domain_use")},ensure_ascii=False)).lower()
            try:
                text += " "+self.resolved(p["text_path"]).read_text(encoding="utf-8",errors="replace").lower()
            except (OSError,KeyError,ValueError):
                pass
            score = sum(min(text.count(w),20) for w in words)
            if score:
                hits.append((score,p))
        return [p for _,p in sorted(hits,key=lambda x:x[0],reverse=True)]

    def context(self,query,source_ids=None,limit=45000,track=None):
        selected = set(source_ids or [])
        papers = self.search(query,track)
        # Explicit sources first; these may not contain the query word.
        if selected:
            candidates=[p for p in self.all_records() if (p["id"] in selected or selected.intersection(p.get("source_aliases",[]))) and (not track or track in self.tracks(p))]
            ranking={p["id"]:i for i,p in enumerate(papers)}
            papers=sorted(candidates,key=lambda p:ranking.get(p["id"],100000))
        else:
            papers = [p for p in papers if p.get("kind") not in {"manuscript"}][:5]
        tokens = re.findall(r"[A-Za-z]{3,}|[\u4e00-\u9fff]{2,}",query.lower())
        parts=[]
        for p in papers[:10]:
            if not self.verify_record(p):
                parts.append(f"SOURCE {p['id']} [source_integrity_failed：副本或文本哈希不符，禁止引用片段。]")
                continue
            try:
                text = self.resolved(p["text_path"]).read_text(encoding="utf-8",errors="replace")
            except (OSError,KeyError,ValueError):
                text = "[全文未提取，不能声称已读]"
            chunks = [text[i:i+3500] for i in range(0,len(text),3500)]
            if len(text)>6500:
                chunks = sorted(enumerate(chunks), key=lambda c:sum(c[1].lower().count(w) for w in tokens), reverse=True)[:2]
                excerpt = "\n[检索片段，非连续全文]\n".join(c for _,c in sorted(chunks))
            else:
                excerpt = text
            boundary="[仅元数据/摘要，禁止据此声称已读全文、公式、证明或实验细节]" if p.get("reading_level") in {"abstract_only","metadata_only"} else ""
            use=f"use={','.join(self.tracks(p))}; read_scope={p.get('read_scope',p.get('reading_level','unknown'))}"
            note=p.get("writing_use","") if track=="writing" else p.get("domain_use","")
            card_key="writing_card_path" if track=="writing" else ("domain_card_path" if p.get("domain_card_path") else "source_card_path")
            if p.get(card_key):
                try:
                    card=self.resolved(p[card_key]).read_text(encoding="utf-8-sig")
                    note=str(note)+"\nCURATED READING CARD (secondary analysis, confirm against source):\n"+card[:4500]
                except (OSError,ValueError):
                    pass
            part = f"SOURCE {p['id']} | {p['title']} | {p.get('kind')} | {use} | SHA256 {p.get('sha256')} {boundary}\nUSE NOTE: {note}\n{excerpt}"
            if sum(len(x) for x in parts)+len(part)>limit:
                break
            parts.append(part)
        return "\n\n".join(parts)
