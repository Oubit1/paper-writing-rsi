/* Render the actual current manuscript PDF, including complex TeX tables. */
(() => {
  "use strict";
  const element=(tag,cls,text)=>{const node=document.createElement(tag);node.className=cls||"";if(text)node.textContent=text;return node;};
  const normalize=text=>String(text||"").normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}]/gu,"");
  function headingPage(pages,title){
    const needle=normalize(title);
    if(!needle)return null;
    // Match heading lines, not a mention of e.g. "datasets" in the abstract.
    const index=pages.findIndex(text=>{
      const lines=String(text).split(/\n/).map(line=>normalize(line.split(/:\s/)[0])).filter(Boolean);
      return lines.some((_,i)=>[1,2,3].some(count=>{
        const line=lines.slice(i,i+count).join("");
        return line.endsWith(needle)&&/^(?:[ivxlcdm]{1,8}|[a-z]|\d{1,3})?$/.test(line.slice(0,-needle.length));
      }));
    });
    return index<0?null:index+1;
  }
  function currentPdf(job,hash){return Boolean(job&&job.build_kind==="current"&&job.source_hash===hash&&job.status==="completed"&&job.pdf_url);}
  let library;
  async function pdfjs(){if(!library)library=import("/static/vendor/pdfjs/pdf.mjs").then(lib=>{lib.GlobalWorkerOptions.workerSrc="/static/vendor/pdfjs/pdf.worker.mjs";return lib;});return library;}
  function mount(host,{projectId,hash,title,api}){
    let disposed=false,pdf=null,loadingTask=null,renderTask=null,textTask=null,renderSerial=0,locationSerial=0,pageNumber=1,zoom=1,pageTexts=[],ready=false,locationNotice="";
    const root=element("section","section-pdf"),toolbar=element("div","section-pdf-toolbar"),viewport=element("div","section-pdf-viewport"),status=element("p","section-pdf-status","正在加载当前稿 PDF…");
    root.setAttribute("aria-label","章节 PDF 阅读器");viewport.setAttribute("aria-label","渲染后的章节 PDF");status.setAttribute("role","status");
    const previous=element("button","","‹"),next=element("button","","›"),pages=element("select"),minus=element("button","","−"),plus=element("button","","＋"),fit=element("button","","适合宽度"),expand=element("button","","放大阅读");
    previous.setAttribute("aria-label","PDF 上一页");next.setAttribute("aria-label","PDF 下一页");pages.setAttribute("aria-label","PDF 页码");minus.setAttribute("aria-label","缩小 PDF");plus.setAttribute("aria-label","放大 PDF");
    const controls=[previous,pages,next,minus,plus,fit,expand];for(const c of controls){c.disabled=true;if(c.tagName==="BUTTON")c.type="button";}
    toolbar.append(...controls);root.append(toolbar,status,viewport);host.replaceChildren(root);host.classList.add("has-section-pdf");
    function renderError(error){if(!disposed&&error.name!=="RenderingCancelledException"&&error.name!=="AbortError")status.textContent="PDF 暂未显示："+error.message;}
    async function render(){
      if(disposed||!pdf||!ready||viewport.clientWidth<1)return;
      const serial=++renderSerial;if(renderTask)renderTask.cancel();if(textTask)textTask.cancel();
      const page=await pdf.getPage(pageNumber);if(disposed||serial!==renderSerial)return;
      const base=page.getViewport({scale:1}),scale=Math.max(.1,(viewport.clientWidth-24)/base.width)*zoom,view=page.getViewport({scale}),ratio=Math.min(window.devicePixelRatio||1,2);
      const sheet=element("div","section-pdf-sheet"),canvas=element("canvas"),layer=element("div","textLayer");
      sheet.style.width=view.width+"px";sheet.style.height=view.height+"px";sheet.style.setProperty("--total-scale-factor",String(scale));
      canvas.width=Math.ceil(view.width*ratio);canvas.height=Math.ceil(view.height*ratio);canvas.style.width=view.width+"px";canvas.style.height=view.height+"px";
      canvas.setAttribute("aria-label","当前稿 PDF 第 "+pageNumber+" 页");sheet.append(canvas,layer);viewport.replaceChildren(sheet);
      previous.disabled=pageNumber<=1;next.disabled=pageNumber>=pdf.numPages;pages.value=String(pageNumber);
      renderTask=page.render({canvasContext:canvas.getContext("2d"),viewport:view,transform:ratio!==1?[ratio,0,0,ratio,0,0]:null});await renderTask.promise;
      if(disposed||serial!==renderSerial)return;
      const lib=await pdfjs(),text=await page.getTextContent();if(disposed||serial!==renderSerial)return;
      textTask=new lib.TextLayer({textContentSource:text,container:layer,viewport:view});await textTask.render();
      if(!disposed&&serial===renderSerial){status.textContent="当前稿 PDF · 第 "+pageNumber+" / "+pdf.numPages+" 页"+locationNotice;root.dataset.renderedPage=String(pageNumber);}
    }
    const redraw=()=>render().catch(renderError);
    function turn(number){pageNumber=Math.max(1,Math.min(pdf.numPages,number));viewport.scrollTop=0;viewport.scrollLeft=0;redraw();}
    previous.addEventListener("click",()=>turn(pageNumber-1));next.addEventListener("click",()=>turn(pageNumber+1));pages.addEventListener("change",()=>turn(Number(pages.value)));
    minus.addEventListener("click",()=>{zoom=Math.max(.5,zoom-.25);redraw();});plus.addEventListener("click",()=>{zoom=Math.min(4,zoom+.25);redraw();});fit.addEventListener("click",()=>{zoom=1;redraw();});
    function expanded(value){root.classList.toggle("expanded",value);expand.textContent=value?"收起阅读":"放大阅读";redraw();}
    expand.addEventListener("click",()=>expanded(!root.classList.contains("expanded")));
    const escape=e=>{if(e.key==="Escape"&&root.classList.contains("expanded")){expanded(false);e.preventDefault();}};document.addEventListener("keydown",escape);
    let resizeTimer,lastWidth=0;
    const observer=new ResizeObserver(entries=>{const width=entries[0].contentRect.width;if(Math.abs(width-lastWidth)<1)return;lastWidth=width;clearTimeout(resizeTimer);resizeTimer=setTimeout(redraw,100);});observer.observe(viewport);
    async function locate(nextTitle){
      title=nextTitle;const serial=++locationSerial;if(!ready)return;
      const found=headingPage(pageTexts,title);if(disposed||serial!==locationSerial)return;
      locationNotice=found?"":" · 未能自动定位此小节，可选择页码";
      turn(found||pageNumber);
    }
    async function load(){
      let job=await api("/api/preview/"+encodeURIComponent(projectId)+"/latest");if(disposed)return;
      if(!currentPdf(job,hash)){
        status.textContent="正在编译当前稿 PDF…";
        job=await api("/api/preview/"+encodeURIComponent(projectId)+"/compile",{base_hash:hash});
      }
      const started=Date.now();
      while(!disposed&&["queued","running"].includes(job?.status)){
        if(Date.now()-started>180000)throw Error("编译仍在进行，请稍后重试。");
        await new Promise(resolve=>setTimeout(resolve,800));if(disposed)return;
        job=await api("/api/preview/"+encodeURIComponent(projectId)+"/jobs/"+encodeURIComponent(job.id));
      }
      if(disposed)return;
      if(!currentPdf(job,hash))throw Error(job?.error||"稿件已更新，请重新打开章节读取最新 PDF。");
      const url=new URL(job.pdf_url,window.location.href);
      if(url.origin!==window.location.origin||!url.pathname.startsWith("/api/preview/"))throw Error("PDF 地址无效。");
      const lib=await pdfjs();if(disposed)return;
      loadingTask=lib.getDocument({url:url.href,isEvalSupported:false,cMapUrl:"/static/vendor/pdfjs/cmaps/",cMapPacked:true,standardFontDataUrl:"/static/vendor/pdfjs/standard_fonts/",wasmUrl:"/static/vendor/pdfjs/wasm/"});
      pdf=await loadingTask.promise;if(disposed)return;
      status.textContent="正在定位章节…";
      for(let number=1;number<=pdf.numPages;number++){
        if(disposed)return;const page=await pdf.getPage(number),text=await page.getTextContent();
        pageTexts.push(text.items.map(item=>(item.str||"")+(item.hasEOL?"\n":" ")).join(""));
        const table=pageTexts.at(-1).match(/\bTABLE\s+([IVXLC\d]+)\b/),option=element("option","","第 "+number+" 页"+(table?" · 表 "+table[1]:""));option.value=String(number);pages.append(option);
      }
      if(disposed)return;ready=true;for(const control of controls)control.disabled=false;await locate(title);
    }
    function failed(error){
      renderError(error);if(disposed)return;
      const retry=element("button","","重新加载 PDF");retry.type="button";
      retry.addEventListener("click",async()=>{
        retry.remove();ready=false;for(const control of controls)control.disabled=true;
        if(loadingTask)await loadingTask.destroy();loadingTask=null;pdf=null;pageTexts=[];pages.replaceChildren();
        status.textContent="正在加载当前稿 PDF…";load().catch(failed);
      });status.after(retry);
    }
    load().catch(failed);
    const controller={locate,destroy(){disposed=true;++renderSerial;++locationSerial;clearTimeout(resizeTimer);observer.disconnect();document.removeEventListener("keydown",escape);renderTask?.cancel();textTask?.cancel();if(loadingTask)Promise.resolve(loadingTask.destroy()).catch(()=>{});}};
    return controller;
  }
  window.paperSectionPdf={mount,headingPage,currentPdf};
})();
