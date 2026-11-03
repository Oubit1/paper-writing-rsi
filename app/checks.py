"""Deterministic diagnostics. Passing these is not a scientific validity proof."""
from __future__ import annotations
import json
import re


OUTPUT_SCHEMA={
    "type":"object","additionalProperties":False,
    "properties":{
        "summary":{"type":"string"},"content":{"type":"string"},
        "decision":{"type":"string","enum":["pass","revise","blocked"]},
        "issues":{"type":"array","items":{"type":"object","additionalProperties":False,
            "properties":{key:{"type":"string"} for key in ("severity","claim","evidence","action")},
            "required":["severity","claim","evidence","action"]}},
        "citations":{"type":"array","items":{"type":"object","additionalProperties":False,
            "properties":{key:{"type":"string"} for key in ("source_id","location","claim")},
            "required":["source_id","location","claim"]}},
        "needs_evidence":{"type":"array","items":{"type":"string"}},
        "assignments":{"type":"array","items":{"type":"object","additionalProperties":False,
            "properties":{key:{"type":"string"} for key in ("role_id","instruction","reason")},
            "required":["role_id","instruction","reason"]}},
        "memory_candidates":{"type":"array","items":{"type":"object","additionalProperties":False,
            "properties":{key:{"type":"string"} for key in ("rule","evidence","scope")},
            "required":["rule","evidence","scope"]}}
    },
    "required":["summary","content","issues","citations","needs_evidence","decision","assignments","memory_candidates"]
}


def parse_response(text):
    raw = text.strip()
    if raw.startswith("```") and raw.endswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    obj = json.loads(raw)
    if not isinstance(obj,dict):
        raise ValueError("角色输出必须为 JSON 对象")
    for field in ("summary", "content", "decision"):
        if not isinstance(obj.get(field),str):
            raise ValueError(f"角色输出缺少字符串 {field}")
    for field in ("issues", "citations", "needs_evidence"):
        if not isinstance(obj.get(field),list):
            raise ValueError(f"角色输出缺少列表 {field}")
    if obj["decision"] not in {"pass","revise","blocked"}:
        raise ValueError("无效 decision")
    if not obj["content"].strip():
        raise ValueError("角色正文为空")
    for issue in obj["issues"]:
        if not isinstance(issue,dict) or not all(isinstance(issue.get(k),str) for k in ("severity","claim","evidence","action")):
            raise ValueError("issues 必须提供 severity/claim/evidence/action")
        if issue["severity"] not in {"critical","major","minor","info"}:
            raise ValueError("无效 issue severity")
    for cite in obj["citations"]:
        if not isinstance(cite,dict) or not all(isinstance(cite.get(k),str) for k in ("source_id","location","claim")):
            raise ValueError("引用必须提供 source_id/location/claim")
    for key in ("assignments","directives"):
        for directive in obj.get(key,[]):
            if not isinstance(directive,dict) or not isinstance(directive.get("role_id"),str) or not isinstance(directive.get("instruction"),str):
                raise ValueError("教师指令须指定角色与修改要求")
    return obj


def validate_content(content, citations, known_sources, formula_limit=33):
    issues = []
    # Search only delimiters and explicit tags; no claim to parse full TeX.
    displays = len(re.findall(r"(?m)^\s*\$\$",content)) // 2 + len(re.findall(r"\\\[",content))
    tags = re.findall(r"\\tag\{([^{}]+)\}",content)
    if content.count("$$") % 2:
        issues.append(dict(severity="major",code="unbalanced_display",message="$$ 展示公式未配对。"))
    if len(tags) != len(set(tags)):
        issues.append(dict(severity="major",code="duplicate_equation_tag",message="存在重复公式编号。"))
    if max(displays,len(tags)) > formula_limit:
        issues.append(dict(severity="minor",code="formula_budget",message=f"展示公式超过 {formula_limit}，需区分正文与补充推导。"))
    for c in citations:
        if c["source_id"] not in known_sources:
            issues.append(dict(severity="major",code="unverified_source",message=f"来源尚未入库核验：{c['source_id']}"))
        if not c["location"].strip():
            issues.append(dict(severity="major",code="missing_location",message=f"引用缺少页码、章节或 URL 位置：{c['source_id']}"))
    for placeholder in re.findall(r"\[(?:TODO|TBD|CITATION NEEDED|待补[^\]]*)\]",content,re.I):
        issues.append(dict(severity="minor",code="placeholder",message=f"保留待补标记：{placeholder}"))
    return dict(formulas=max(displays,len(tags)),tags=tags,issues=issues,
                passed=not any(i["severity"] in {"critical","major"} for i in issues),
                scope="机械检查；不证明公式正确、引用支持主张或实验有效。")


def gate(reviewer, calibration, teacher, artifacts, mock=False):
    reasons = []
    for name, obj in (("reviewer",reviewer),("calibration",calibration),("global_teacher",teacher)):
        if not obj or obj.get("decision") != "pass":
            reasons.append(f"{name}: 尚未通过")
        elif any(i.get("severity") in {"critical","major"} for i in obj.get("issues",[])):
            reasons.append(f"{name}: 仍有重要问题")
        if obj and obj.get("needs_evidence"):
            reasons.append(f"{name}: 仍有待补证据")
    for a in artifacts:
        if not a["validation"].get("passed",False):
            reasons.append(f"{a['role_id']}: 来源或公式机械检查未通过")
        payload=a.get("payload",{})
        if payload.get("decision") != "pass":
            reasons.append(f"{a['role_id']}: 作者仍声明需修改或无法完成")
        if payload.get("needs_evidence") or any(i.get("severity") in {"critical","major"} for i in payload.get("issues",[])):
            reasons.append(f"{a['role_id']}: 作者仍有重要问题或证据债务")
    if mock:
        reasons.append("离线演练不晋级真实稿件或经验")
    return {"passed":not reasons,"reasons":reasons,
            "meaning":"本轮写作评议通过；不代表科研结论成立、实验完成或跨题目能力已改善。"}
