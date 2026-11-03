"""Bounded ZIP project import; uploaded archive contents are never executed."""
from pathlib import Path,PurePosixPath
import io,re,stat,tempfile,zipfile,shutil
from .source_review import normalized,validate_source
from .latex_source import _mask_comments
from .store import uid

MAX_ZIP=64*1024*1024
MAX_EXPANDED=256*1024*1024

def extract_archive(raw,folder):
    if not raw or len(raw)>MAX_ZIP:raise ValueError('ZIP 最大支持 64 MB')
    try:archive=zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:raise ValueError('不是有效的 ZIP 文件') from exc
    with archive:
        entries=archive.infolist()
        if len(entries)>2000:raise ValueError('ZIP 文件条目过多')
        total=0;seen=set();files=[]
        for entry in entries:
            name=entry.orig_filename
            path=PurePosixPath(name)
            if '\\' in name or ':' in name or path.is_absolute() or '..' in path.parts or '\x00' in name:raise ValueError('ZIP 包含不安全路径')
            if stat.S_ISLNK(entry.external_attr>>16) or entry.flag_bits&1:raise ValueError('不支持链接或加密 ZIP')
            if any(p in ('.git','__MACOSX') for p in path.parts) or path.name=='.DS_Store':continue
            if entry.is_dir():continue
            key=path.as_posix().casefold()
            if key in seen:raise ValueError('ZIP 存在重复或大小写冲突的文件名')
            seen.add(key);total+=entry.file_size
            if total>MAX_EXPANDED or entry.file_size>MAX_ZIP or len(files)>=500:raise ValueError('解压内容过大或文件超过 500 个')
            files.append((entry,path))
        for entry,path in files:
            target=folder.joinpath(*path.parts)
            if not target.resolve().is_relative_to(folder.resolve()):raise ValueError('ZIP 路径越界')
            target.parent.mkdir(parents=True,exist_ok=True)
            try:
                with archive.open(entry) as source,target.open('xb') as output:
                    shutil.copyfileobj(source,output)
            except (zipfile.BadZipFile,RuntimeError,NotImplementedError) as exc:raise ValueError('ZIP 文件损坏或压缩方式不支持') from exc

def main_entries(folder):
    result=[]
    for p in sorted(folder.rglob('*.tex')):
        if p.stat().st_size>5_000_000:continue
        try:text=p.read_text(encoding='utf-8-sig')
        except UnicodeError:continue
        if re.search(r'\\documentclass(?:\s*\[[^\]]*\])?\s*\{',_mask_comments(text)) and re.search(r'\\begin\s*\{document\}',_mask_comments(text)):
            result.append(p.relative_to(folder).as_posix())
    return result

def flatten(main,assets):
    def read(path,stack=()):
        path=path.resolve()
        if not path.is_relative_to(assets.resolve()) or path.suffix.lower()!='.tex' or not path.is_file():raise ValueError('章节引用不存在或超出工程目录')
        if path in stack or len(stack)>30:raise ValueError('章节引用循环或层级过深')
        try:text=normalized(path.read_text(encoding='utf-8-sig'))
        except UnicodeError as exc:raise ValueError('请将 TeX 源码保存为 UTF-8 编码') from exc
        matches=list(re.finditer(r'\\(?:input|include)\s*\{([^{}]+)\}',_mask_comments(text)))
        for match in reversed(matches):
            name=match.group(1).strip()
            if '\\' in name or ':' in name or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts:raise ValueError('章节引用路径不安全')
            relative=Path(name if Path(name).suffix else name+'.tex')
            target=assets/relative
            if not target.is_file():target=path.parent/relative
            child=read(target,stack+(path,))
            text=text[:match.start()]+'\n'+child+'\n'+text[match.end():]
            if len(text)>5_000_000:raise ValueError('展开后的正文过大')
        return text
    text=read(main);validate_source(text);return text

def import_project(root,store,review,raw,name,entry=None):
    root=Path(root).resolve();workspace=root/'runtime/uploads';workspace.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=workspace) as scratch:
        folder=Path(scratch);extract_archive(raw,folder);entries=main_entries(folder)
        if not entries:raise ValueError('ZIP 中未找到包含 document 环境的 UTF-8 LaTeX 主文件')
        preferred=[p for p in entries if PurePosixPath(p).name.lower()=='main.tex']
        if entry is None:
            if len(entries)==1:entry=entries[0]
            elif len(preferred)==1:entry=preferred[0]
            else:return dict(requires_entry=True,entries=entries)
        if entry not in entries:raise ValueError('请选择 ZIP 中有效的 LaTeX 主文件')
        main=folder/entry;tex=flatten(main,main.parent)
        project=store.create_project((Path(str(name)).stem.strip() or '导入的论文')[:160])
        destination=root/'projects'/project['id']/'uploaded'
        try:
            shutil.copytree(folder,destination)
            assets=(destination/entry).parent
            original=destination.parent/'source_review'/'imported.tex';original.parent.mkdir(parents=True,exist_ok=True);original.write_text(tex,encoding='utf-8')
            document=review.initialize_upload(project['id'],original,assets)
        except Exception:
            # Only this newly allocated project can be rolled back.
            if not review.active(project['id']):store.execute('DELETE FROM projects WHERE id=?',(project['id'],))
            raise
        return dict(project=project,source=document,entry=entry,requires_entry=False)
