import io,json,stat,tempfile,unittest,zipfile,urllib.request,urllib.error
from pathlib import Path
from unittest.mock import patch
from app.store import Store
from app.source_review import SourceReviewStore
from app.project_upload import import_project,extract_archive

MAIN=r'\documentclass{article}\begin{document}\section{Introduction}Hello.\end{document}'
def zipped(files):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,value in files:archive.writestr(name,value)
    return buffer.getvalue()

class UploadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.store=Store(self.root);self.review=SourceReviewStore(self.root,self.store)
    def tearDown(self):self.temp.cleanup()
    def upload(self,files,entry=None):return import_project(self.root,self.store,self.review,zipped(files),'Research.zip',entry)
    def test_nested_chapters_assets_and_no_proposals(self):
        main=r'\documentclass{article}\begin{document}\input{sections/intro}\end{document}'
        result=self.upload([('paper/main.tex',main),('paper/sections/intro.tex',r'\section{Introduction}Hello.'),('paper/images/a.png',b'fixture')])
        doc=result['source'];self.assertIn('Hello.',doc['tex']);self.assertNotIn(r'\input',doc['tex']);self.assertEqual(doc['proposals'],[])
        self.assertEqual(doc['revision'],1);self.assertTrue((self.root/doc['assets_path']/'images/a.png').exists())
        self.assertIn('images/a.png',[f['name'] for f in doc['files']])
        self.assertTrue(self.review.file(result['project']['id'],'images/a.png')['binary'])
    def test_existing_manuscript_is_not_replaced(self):
        first=self.upload([('main.tex',MAIN)]);second=self.upload([('main.tex',MAIN.replace('Hello.','Different.'))])
        self.assertNotEqual(first['project']['id'],second['project']['id'])
        self.assertEqual(self.review.get(first['project']['id'])['tex'],MAIN)
    def test_ambiguous_entries_require_choice_without_creating_project(self):
        files=[('a.tex',MAIN),('b.tex',MAIN)]
        result=self.upload(files);self.assertTrue(result['requires_entry']);self.assertEqual(self.store.query('SELECT id FROM projects'),[])
        self.assertEqual(self.upload(files,'b.tex')['entry'],'b.tex')
    def test_unique_main_is_preferred(self):
        self.assertEqual(self.upload([('a.tex',MAIN),('main.tex',MAIN)])['entry'],'main.tex')
    def test_traversal_absolute_and_duplicate_paths(self):
        for name in ('../outside.tex','/outside.tex','C:/outside.tex'):
            with self.subTest(name=name),self.assertRaises(ValueError):self.upload([(name,MAIN)])
        raw=zipped([('a/outside.tex',MAIN)]).replace(b'a/outside.tex',b'a\\outside.tex')
        with self.assertRaises(ValueError):extract_archive(raw,self.root/'unsafe')
        with self.assertRaises(ValueError):self.upload([('MAIN.tex',MAIN),('main.tex',MAIN)])
        self.assertEqual(self.store.query('SELECT id FROM projects'),[])
    def test_symlink_and_expansion_limit(self):
        link=zipfile.ZipInfo('main.tex');link.create_system=3;link.external_attr=(stat.S_IFLNK|0o777)<<16
        with self.assertRaises(ValueError):self.upload([(link,'target')])
        with patch('app.project_upload.MAX_EXPANDED',10),self.assertRaises(ValueError):self.upload([('main.tex',MAIN)])
    def test_invalid_or_missing_chapter_does_not_create_project(self):
        for files in ([('readme.md','No TeX')],[('main.tex',MAIN.replace('Hello.',r'\input{missing}'))],[('main.tex',MAIN.replace('Hello.',r'\input{../escape}'))]):
            with self.assertRaises(ValueError):self.upload(files)
        self.assertEqual(self.store.query('SELECT id FROM projects'),[])
    def test_cyclic_chapter_reference(self):
        with self.assertRaises(ValueError):self.upload([('main.tex',MAIN.replace('Hello.',r'\input{child}')),('child.tex',r'\input{child}')])
