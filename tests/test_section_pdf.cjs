const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const window={};vm.runInNewContext(fs.readFileSync(__dirname+'/../static/section-pdf.js','utf8'),{window});
const {headingPage,currentPdf}=window.paperSectionPdf;
const pages=['Abstract\nWe evaluate on three datasets.\n',
  'III. E XPERIMENTS AND R ESULTS\nA. Experimental Settings\n1) Datasets\n',
  'B. Experimental Results\n1) Comparison with state-of-the-art\nmethods\n'];
assert.equal(headingPage(pages,'Experiments and Results'),2);
assert.equal(headingPage(pages,'Datasets'),2,'Never jump to a body-text mention in the abstract');
assert.equal(headingPage(pages,'Comparison with state-of-the-art methods'),3);
assert.equal(headingPage(pages,'Component Ablation'),null);
assert.equal(headingPage(['1) Comparison with state-of-the-art methods: To exam-\nine whether cine-only MI localization can be solved'],'Comparison with state-of-the-art methods'),1,'IEEE run-in headings end at a colon');
const job={build_kind:'current',source_hash:'h1',status:'completed',pdf_url:'/api/preview/p/pdf/j'};
assert.equal(currentPdf(job,'h1'),true);
assert.equal(currentPdf(job,'h2'),false,'Old PDF must not stand in for the current source');
assert.equal(currentPdf({...job,build_kind:'candidate'},'h1'),false);
assert.equal(currentPdf({...job,status:'failed'},'h1'),false);
assert.equal(currentPdf(null,'h1'),false);
console.log('10 section PDF heading and source-version checks passed.');
