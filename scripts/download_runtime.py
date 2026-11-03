"""Explicit installer: obtain runtime files from their original public sources."""
from pathlib import Path
import hashlib,json,urllib.request,zipfile,tempfile,shutil
ROOT=Path(__file__).resolve().parents[1]
def fetch(url,target):
    req=urllib.request.Request(url,headers={'User-Agent':'PaperAtelier-installer'})
    with urllib.request.urlopen(req,timeout=90) as response,target.open('wb') as output:
        shutil.copyfileobj(response,output)
def digest(path):
    with path.open('rb') as file:return hashlib.file_digest(file,'sha256').hexdigest()
def member(archive,name):
    matches=[n for n in archive.namelist() if n==name or n.endswith('/'+name)]
    if len(matches)!=1:raise ValueError('Missing or ambiguous archive entry: '+name)
    return matches[0]
def install():
    for lang in ('en-zh','zh-en'):
        folder=ROOT/'tools/translation/models'/lang
        manifest=json.loads((folder/'source_manifest.json').read_text(encoding='utf-8-sig'))
        if all((folder/name).exists() and digest(folder/name)==sha for name,sha in manifest['files'].items()):continue
        url=manifest['source_url']
        if not url.startswith('https://data.argosopentech.com/argospm/'):raise ValueError('Unexpected model source')
        print('Downloading',lang,flush=True)
        with tempfile.TemporaryDirectory() as scratch:
            package=Path(scratch)/'model.zip';fetch(url,package)
            if digest(package)!=manifest['package_sha256']:raise ValueError('Model package checksum failed')
            with zipfile.ZipFile(package) as archive:
                for name,sha in manifest['files'].items():
                    # Preserve provenance/README already shipped; extract only expected runtime files.
                    if name in ('README.md','metadata.json'):continue
                    target=(folder/name).resolve()
                    if not target.is_relative_to(folder.resolve()):raise ValueError('Invalid model path')
                    data=archive.read(member(archive,name))
                    if hashlib.sha256(data).hexdigest()!=sha:raise ValueError('Model file checksum failed: '+name)
                    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    compiler=ROOT/'tools/tectonic/tectonic.exe'
    if not compiler.exists():
        print('Downloading official Tectonic Windows x64 release',flush=True)
        req=urllib.request.Request('https://api.github.com/repos/tectonic-typesetting/tectonic/releases/latest',headers={'User-Agent':'PaperAtelier-installer'})
        with urllib.request.urlopen(req,timeout=30) as response:release=json.load(response)
        asset=next(a for a in release['assets'] if a['name'].endswith('x86_64-pc-windows-msvc.zip'))
        url=asset['browser_download_url']
        if not url.startswith('https://github.com/tectonic-typesetting/tectonic/releases/download/'):raise ValueError('Unexpected compiler source')
        with tempfile.TemporaryDirectory() as scratch:
            package=Path(scratch)/'compiler.zip';fetch(url,package)
            expected=asset.get('digest') or ''
            if expected.startswith('sha256:') and digest(package)!=expected[7:]:raise ValueError('Compiler checksum failed')
            with zipfile.ZipFile(package) as archive:
                data=archive.read(member(archive,'tectonic.exe'))
            compiler.parent.mkdir(parents=True,exist_ok=True);compiler.write_bytes(data)
    print('Local runtime ready.')
if __name__=='__main__':install()
