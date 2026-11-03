"""Bounded background discovery using public topic names, never manuscript text."""
import hashlib
import html
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from .store import now

TOPICS=[('myocardial infarction',r'infarct|梗死'),('cardiac magnetic resonance',r'cine|cmr|cardiac|心脏|磁共振'),
        ('image segmentation',r'segment|分割|定位'),('state space model',r'state.space|状态空间|lssm'),
        ('variational inference',r'variational|posterior|变分|后验'),('deep learning',r'neural|deep.learning|神经|深度学习')]

def public_topics(text):
    return [topic for topic,pattern in TOPICS if re.search(pattern,text,re.I)][:4]


class LibraryEnrichment:
    def __init__(self,library,store):
        self.library,self.store=library,store
        self.lock=threading.Lock();self.running=set()
        store.execute("CREATE TABLE IF NOT EXISTS library_enrichment (project_id TEXT PRIMARY KEY,query TEXT,status TEXT,updated REAL,result TEXT)")

    def start(self,pid):
        project=self.store.project(pid)
        if not project:raise ValueError('课题不存在')
        consent=self.library.root/'configs/library_enrichment.json'
        if not consent.exists() or json.loads(consent.read_text(encoding='utf-8-sig')).get('crossref_public_topics') is not True:
            return dict(status='permission_required',added=0)
        source=self.store.query("SELECT tex FROM source_documents WHERE project_id=?",(pid,),one=True)
        topics=public_topics(project['name']+' '+project['task']+' '+(source['tex'] if source else ''))
        if not topics:return dict(status='no_public_topics',added=0)
        query=' '.join(topics)
        with self.lock:
            row=self.store.query('SELECT * FROM library_enrichment WHERE project_id=?',(pid,),one=True)
            if pid in self.running:return dict(status='running')
            if row and row['query']==query and time.time()-row['updated']<86400 and row['status']=='completed':return json.loads(row['result'])
            self.running.add(pid)
            self.store.execute('INSERT OR REPLACE INTO library_enrichment VALUES(?,?,?,?,?)',(pid,query,'running',time.time(),'{}'))
            threading.Thread(target=self._run,args=(pid,query,topics),daemon=True).start()
        return dict(status='running',query=query)

    def _run(self,pid,query,topics):
        result=dict(status='completed',query=query,added=0,source_ids=[])
        try:
            url='https://api.crossref.org/works?'+urllib.parse.urlencode({'query.bibliographic':query,'rows':6,'filter':'type:journal-article'})
            request=urllib.request.Request(url,headers={'User-Agent':'PaperAtelier/0.2 (local literature discovery)','Accept':'application/json'})
            with urllib.request.urlopen(request,timeout=20) as response:
                raw=response.read(2*1024*1024+1)
            if len(raw)>2*1024*1024:raise ValueError('Metadata response too large')
            items=json.loads(raw)['message']['items']
            known={(p.get('doi') or '').lower() for p in self.library.records()}
            for item in items:
                doi=(item.get('DOI') or '').lower();titles=item.get('title') or []
                if not doi or not titles or doi in known:continue
                title=html.unescape(re.sub('<[^>]+>','',titles[0]))
                abstract=html.unescape(re.sub('<[^>]+>',' ',item.get('abstract','')))
                # Require two topic word matches in deposited title/abstract.
                words=set(re.findall(r'[a-z]{4,}',query));content=(title+' '+abstract).lower()
                if sum(w in content for w in words)<2:continue
                data=json.dumps(dict(title=title,doi=doi,abstract=abstract,source='Crossref deposited metadata',retrieved_at=now()),ensure_ascii=False,indent=2).encode('utf-8')
                sha=hashlib.sha256(data).hexdigest();sid='discovery_'+hashlib.sha256(doi.encode()).hexdigest()[:16]
                folder=self.library.base/'discovery';folder.mkdir(parents=True,exist_ok=True)
                file=folder/(sid+'.txt');file.write_bytes(data);path=file.relative_to(self.library.root).as_posix()
                record=dict(id=sid,title=title,doi=doi,url='https://doi.org/'+doi,kind='reference',tracks=['domain'],
                    path=path,text_path=path,sha256=sha,text_sha256=sha,domain_topics=topics,
                    reading_level='abstract_only' if abstract else 'metadata_only',read_scope='Crossref元数据'+('与摘要' if abstract else '；未获取全文'),
                    imported_at=now(),discovered_for=pid)
                self.library._save(record);known.add(doi);result['source_ids'].append(sid);result['added']+=1
        except Exception as exc:
            result.update(status='failed',error=str(exc)[:500])
        finally:
            self.store.execute('UPDATE library_enrichment SET status=?,updated=?,result=? WHERE project_id=?',(result['status'],time.time(),json.dumps(result,ensure_ascii=False),pid))
            with self.lock:self.running.discard(pid)
