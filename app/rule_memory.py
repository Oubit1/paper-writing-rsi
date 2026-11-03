"""Replay durable author constraints; scores measure coverage, not correctness."""
import json
import re
from .store import digest


def collect_rules(store, pid, scope="document", current="", budget=14000):
    messages=[]
    tables={r["name"] for r in store.query("SELECT name FROM sqlite_master WHERE type='table'")}
    if "source_agent_jobs" in tables:
        messages += [dict(id=r["id"],text=r["message"],scope=r["scope"],date=r["created_at"]) for r in
                     store.query("SELECT id,message,scope,created_at FROM source_agent_jobs WHERE project_id=? ORDER BY rowid",(pid,))]
    if "messages" in tables:
        messages += [dict(id=r["id"],text=r["content"],scope="document",date=r["created_at"]) for r in
                     store.query("SELECT id,content,created_at FROM messages WHERE project_id=? AND sender='user' ORDER BY created_at",(pid,))]
    messages.sort(key=lambda r:r["date"])
    messages.append(dict(id="current",text=current,scope=scope,date="current"))
    rules={}
    for message in messages:
        if scope!="document" and message["scope"] not in {scope,"document"}:
            continue
        # Preserve exact author clauses. Do not infer permanent rules from model prose.
        for clause in re.split(r"(?<=[。！？!?])\s*|\n+",message["text"]):
            clause=clause.strip()
            if not clause or not re.search(r"不要|必须|需要|保持|保留|不能|不允许|始终|标准|规则|避免|禁止|仅|只|不超过|must|never|always|do not|preserve",clause,re.I):
                continue
            key="rule_"+digest(clause)[:12]
            rules[key]=dict(id=key,text=clause,source_id=message["id"],scope=message["scope"])
    ordered=sorted(rules.values(),key=lambda r:r["source_id"]!="current")
    included=[];used=0
    for rule in ordered:
        size=len(json.dumps(rule,ensure_ascii=False))
        if used+size<=budget:included.append(rule);used+=size
    return dict(rules=included,total=len(ordered),included=len(included),
                coverage=round(len(included)/len(ordered),3) if ordered else 1,
                omitted_ids=[r["id"] for r in ordered if r not in included],
                policy="当前用户要求优先。历史约束有冲突时明确指出冲突及依据，不静默删除。覆盖率不是遵从率。")


def audit_rules(memory,checks):
    checks=checks if isinstance(checks,list) else []
    valid={c.get("id"):c for c in checks if isinstance(c,dict) and c.get("status") in {"met","conflict","not_applicable","unverified"} and isinstance(c.get("evidence"),str) and c["evidence"].strip()}
    rows=[dict(rule,**{k:valid.get(rule["id"],{}).get(k,default) for k,default in [("status","unverified"),("evidence","")]}) for rule in memory["rules"]]
    checked=sum(r["id"] in valid for r in rows)
    return dict(context_coverage=memory["coverage"],check_coverage=round(checked/len(rows),3) if rows else 1,
                rules=rows,omitted_ids=memory["omitted_ids"],independently_verified=False,
                needs_review=bool(memory["omitted_ids"]) or any(r["status"] in {"unverified","conflict"} for r in rows))
