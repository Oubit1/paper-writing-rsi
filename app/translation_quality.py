"""Observable translation checks, never a fabricated semantic accuracy score."""
import re
from collections import Counter

CHECKS=[(r'\binfarct mask\b',('梗死灶掩膜',)),(r'\bresidual posterior\b',('残差后验',)),
        (r'\blatent states?\b',('隐状态','潜在状态')),(r'\bposterior wall\b',('后壁',)),
        (r'\bcontrast agents?\b',('造影剂','对比剂')),(r'\bfive-fold cross-validation\b',('五折交叉验证',))]

def quality_checks(source,target,language):
    terms=[dict(source=m.group(),expected=list(expected),passed=any(t in target for t in expected)) for pattern,expected in CHECKS
           if language=='zh-CN' for m in [re.search(pattern,source,re.I)] if m]
    if language=='en':
        for phrase,expected in [('造影剂',['contrast agent','contrast medium']),('心肌梗死',['myocardial infarction']),('后壁',['posterior wall'])]:
            if phrase in source:terms.append(dict(source=phrase,expected=expected,passed=any(t in target.lower() for t in expected)))
    numbers=Counter(re.findall(r'\d+(?:[.,]\d+)*',source))==Counter(re.findall(r'\d+(?:[.,]\d+)*',target))
    return dict(terminology=terms,terms_passed=sum(t['passed'] for t in terms),terms_checked=len(terms),
                numbers_preserved=numbers,unknown_tokens=bool(re.search(r'⁇|�|<unk>',target)),
                semantic_accuracy=None,semantic_status='requires_reference_or_human_review')
