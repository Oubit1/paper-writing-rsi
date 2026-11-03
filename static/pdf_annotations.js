"use strict";
(function(root){
  function normalizeMapped(text){
    let normalized="",offset=0;const positions=[];
    for(const char of String(text||"")){
      const folded=char.normalize("NFKD").replace(/[\u0300-\u036f]/g,"").toLowerCase();
      for(const value of folded){if(/[\p{L}\p{N}]/u.test(value)){normalized+=value;for(let i=0;i<value.length;i++)positions.push({start:offset,end:offset+char.length});}}
      offset+=char.length;
    }
    return {text:normalized,positions};
  }
  function visibleTex(value){
    return String(value||"").replace(/(^|[^\\])%[^\n]*/g,"$1").replace(/\\IEEEPARstart\{([^{}]*)\}\{([^{}]*)\}/g,"$1$2")
      .replace(/\\(?:cite[pt]?|label|ref|eqref)\*?(?:\[[^\]]*\])?\{[^{}]*\}/g," ")
      .replace(/\\(?:textbf|textit|textrm|textsf|emph|mbox|caption|section|subsection|subsubsection)\*?(?:\[[^\]]*\])?\{([^{}]*)\}/g,"$1")
      .replace(/\\(?:&|%|_|#)/g,m=>m.slice(1)).replace(/\\[A-Za-z@]+\*?(?:\[[^\]]*\])?/g," ").replace(/[{}~]/g," ").replace(/--+/g,"-");
  }
  function mathTarget(value){return /\$|\\[\[(]|\\begin\{(?:equation|align|gather|multline|math)/.test(value||"");}
  function fragmentText(node){if(node.nodeType===3)return node.nodeValue||"";if(String(node.tagName||"").toUpperCase()==="BR")return "\n";return Array.from(node.childNodes||[]).map(fragmentText).join("");}
  function occurrences(haystack,needle){const hits=[];if(!needle)return hits;let at=haystack.indexOf(needle);while(at>=0){hits.push(at);if(hits.length>200)return hits;at=haystack.indexOf(needle,at+1);}return hits;}
  function commonSuffix(a,b){let n=0;while(n<Math.min(a.length,b.length)&&a[a.length-1-n]===b[b.length-1-n])n++;return n;}
  function commonPrefix(a,b){let n=0;while(n<Math.min(a.length,b.length)&&a[n]===b[n])n++;return n;}
  function headingMatch(pages,title){
    const query=normalizeMapped(visibleTex(title)).text;if(!query)return null;
    const headings=[],fallback=[];
    for(const page of pages){
      for(const start of occurrences(page.text,query))fallback.push({page:page.page,start,end:start+query.length,query});
      let offset=0;for(const fragment of page.fragments||[]){const line=normalizeMapped(fragment).text;
        if(line===query||(line.endsWith(query)&&/^(?:[ivxlcdm]+|\d+)$/.test(line.slice(0,-query.length)))){const start=offset+line.length-query.length;headings.push({page:page.page,start,end:start+query.length,query});}
        offset+=line.length;
      }
    }
    return headings[0]||fallback[0]||null;
  }
  function matchVisible(pages,tex,context={}){
    if(mathTarget(tex))return {reason:"公式目标未建立可靠 PDF 文字锚点，可在问题列表审阅。"};
    const query=normalizeMapped(visibleTex(tex)).text;if(query.length<4)return {reason:"目标词过短，未标注可能有歧义的位置。"};
    const before=normalizeMapped(visibleTex(context.before||"")).text.slice(-100),after=normalizeMapped(visibleTex(context.after||"")).text.slice(0,100),hits=[];
    for(const page of pages){for(const start of occurrences(page.text,query)){const end=start+query.length;hits.push({page:page.page,start,end,query,score:commonSuffix(page.text.slice(Math.max(0,start-100),start),before)+commonPrefix(page.text.slice(end,end+100),after)});}}
    if(hits.length===1)return hits[0];
    hits.sort((a,b)=>b.score-a.score);
    if(hits.length>1&&hits[0].score>=Math.min(18,before.length+after.length)&&hits[0].score>hits[1].score+5)return hits[0];
    return {reason:hits.length?"PDF 中有多处相同文字，未猜测具体位置。":"尚未在单页 PDF 中找到完整目标；可能涉及断页、数学或排版转换。"};
  }
  function issueAnchors(pages,issues){
    const anchors=new Map(),unmatched=new Map();
    for(const issue of issues){
      let found=null,reason="该问题暂未匹配到 PDF 文字。";
      const edits=(issue.edits||issue.hunks||[]).filter(h=>!h.status||h.status==="pending");
      for(const h of edits){
        const context=issue.context||{},base=String(context.original||""),original=String(h.original_tex||""),pos=original?base.indexOf(original):-1;
        const around=pos>=0?{before:String(context.before||"")+base.slice(0,pos),after:base.slice(pos+original.length)+String(context.after||"")}:context;
        const match=matchVisible(pages,original,around);if(match.page){found={...match,proposalId:issue.id,hunkId:h.id,title:issue.title};break;}reason=match.reason;
      }
      if(found)anchors.set(issue.id,found);else unmatched.set(issue.id,reason);
    }
    return {anchors,unmatched};
  }
  const api={normalizeMapped,visibleTex,mathTarget,fragmentText,headingMatch,matchVisible,issueAnchors};
  if(typeof module!=="undefined"&&module.exports)module.exports=api;else root.PaperPdfReview=api;
})(typeof window!=="undefined"?window:globalThis);

