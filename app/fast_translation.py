"""Strictly local CTranslate2/SentencePiece translation; no download APIs.

The configured files are deployed separately. Loading this module neither imports
large model runtimes nor scans user-wide package stores or contacts a service.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import threading

ADAPTER_VERSION = "ct2-spm-offline-v4-compact"


EXACT_TERMS = {"anchor": "锚点", "residual": "残差", "readout": "读出", "state": "状态",
    "latent": "潜在的", "latent state": "隐状态", "latent states": "隐状态", "posterior": "后验",
    "prior": "先验", "residual posterior": "残差后验", "conditional residual posterior": "条件残差后验",
    "variational posterior": "变分后验", "contrast-free": "无造影剂", "phenotype": "表型",
    "motion phenotype": "运动表型", "myocardial infarction": "心肌梗死", "myocardial infarct": "心肌梗死灶",
    "mask": "掩膜", "infarct": "梗死灶", "infarct mask": "梗死灶掩膜",
    "cine": "电影磁共振序列", "cine cmr": "心脏电影磁共振", "cine mri": "电影磁共振成像",
    "latent-state inference": "隐状态推断", "latent state inference": "隐状态推断",
    "readout-coupled forward generation": "读出耦合前向生成",
    "contrast-free myocardial infarct localization": "无造影剂心肌梗死定位",
    "contrast-free myocardial infarct segmentation": "无造影剂心肌梗死分割",
    "contrast-free myocardial infarction localization": "无造影剂心肌梗死定位",
    "contrast-free myocardial infarction segmentation": "无造影剂心肌梗死分割",
    "hierarchical prior-guided state decomposition": "层次先验引导的状态分解",
    "task formulation and probabilistic state inference": "任务表述与概率状态推断",
    "variational learning and optimization": "变分学习与优化",
    "architecture-specific implementation": "架构特定实现"}


def calibrate_terms(source, translated, target_language):
    """Correct only observed ambiguities under conservative source conditions.

    Call this before restoring protected spans. These repairs are not a quality
    score and do not align or replace arbitrary terminology across whole text.
    """
    if target_language == "en" and "造影剂" in source:
        fixed,n=re.subn(r'film-making agents?', 'contrast agent', translated, flags=re.I)
        if n:
            return fixed,[dict(source="造影剂",original="film-making agent",replacement="contrast agent",count=n,reason="源文明确为医学造影剂，校准已观测歧义。")]
    if target_language != "zh-CN":
        return translated, []
    source = source.casefold()
    adjustments = []

    def has(pattern):
        return bool(re.search(pattern, source))

    def repair(term, pattern, replacement, rationale):
        nonlocal translated
        matches = list(re.finditer(pattern, translated))
        if not matches:
            return
        originals = list(dict.fromkeys(match.group() for match in matches))
        translated = re.sub(pattern, replacement, translated)
        adjustments.append(dict(source=term, original=" / ".join(originals), replacement=replacement,
                                count=len(matches), reason=rationale))

    physical_mask = has(r"\b(?:face|oxygen|surgical|protective|respiratory)\s+masks?\b")
    if not physical_mask and has(r"\binfarct\s+mask\b") and has(r"\b(?:predict\w*|segment\w*)\b"):
        repair("infarct mask", r"发作面具|梗塞面具", "梗死灶掩膜", "源文明确指预测/分割的infarct mask，校准已观测的复合错译。")
    if not physical_mask and has(r"\bmask\b") and has(r"\b(?:predict\w*|segment\w*|infarct\w*)\b"):
        repair("mask", r"面具", "掩膜", "源文mask与预测/分割/梗死共同出现，并排除了实体防护面罩语境。")
    if has(r"\bresidual\s+posterior\b") and not has(r"\bposterior\s+wall\b|\bremaining\b"):
        repair("residual posterior", r"剩余后部|剩余后遗症|剩余后缀", "残差后验", "源文明确为residual posterior，未出现posterior wall或remaining。")
        if has(r"\bthe prior\b"):
            repair("prior", r"前缀|前部", "先验", "源文同时明确使用residual posterior及the prior。")
    if has(r"\bthe prior\b") and has(r"\buncertainty\b"):
        repair("prior", r"前一个运动|之前的运动", "先验", "源文在不确定性语境下明确使用名词the prior。")
    if has(r"\blatent state\b"):
        repair("latent state", r"潜伏状态", "隐状态", "源文明确为latent state。")
    if has(r"\bposterior wall\b") and has(r"\bmyocardium\b"):
        repair("posterior wall", r"后墙", "后壁", "源文明确为心肌后壁，不是概率后验。")
    if has(r"\bfive-fold cross-validation\b"):
        repair("five-fold cross-validation", r"五倍的交叉验证法|五次交叉验证", "五折交叉验证", "源文明确指定五折交叉验证。")
    if has(r"\bcine\b") and not has(r"\b(?:film|movie|cinema)\w*\b"):
        repair("cine", r"(?<!心脏)(?:电影|影视|焚化)(?!磁共振|CMR|MRI)", "心脏电影磁共振", "源文cine未出现film/movie/cinema语境；校准项目已观测歧义。")
    if has(r"\bphenotype\b") and not has(r"phenyl"):
        repair("phenotype", r"现象型|苯基", "表型", "源文包含phenotype且不含phenyl化学语境。")
    return translated, adjustments


class LocalTranslationError(ValueError):
    code = "local_unavailable"


class LocalTranslationCancelled(LocalTranslationError):
    code = "cancelled"


class FastTranslator:
    name = "ctranslate2_local"

    def __init__(self, root):
        self.root = Path(root).resolve()
        self._lock = threading.RLock()
        self._hashes = {}
        self._models = {}

    def prewarm(self):
        """Load in the background, never on the first selection click."""
        try:
            config = json.loads((self.root / "configs/translation.json").read_text(encoding="utf-8-sig"))
            if not config.get("prewarm"):
                return
            for entry in config.get("models", []):
                self._load(self.describe(entry["source_language"], entry["target_language"], "warmup sentence"))
        except (OSError, ValueError, KeyError):
            return

    def _path(self, value, directory=False):
        if not isinstance(value, str) or not value:
            raise LocalTranslationError("离线翻译配置缺少模型或分词文件路径。")
        path = (self.root / value).resolve()
        if not path.is_relative_to(self.root) or not (path.is_dir() if directory else path.is_file()):
            raise LocalTranslationError("离线翻译模型尚未部署或路径超出工作区；可显式选择 Agent 校订。")
        return path

    def _hash(self, path):
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        with self._lock:
            if key not in self._hashes:
                sha = hashlib.sha256()
                with path.open("rb") as file:
                    for block in iter(lambda: file.read(1024 * 1024), b""):
                        sha.update(block)
                self._hashes[key] = sha.hexdigest()
            return self._hashes[key]

    @staticmethod
    def _direction(source, target, text):
        if source != "auto":
            return source
        chinese = len(re.findall(r"[\u3400-\u9fff]", text))
        latin = len(re.findall(r"[A-Za-z]", text))
        return "zh-CN" if chinese and chinese / max(1, chinese + latin) > .2 else "en"

    @staticmethod
    def _tokens(value, name):
        # An empty/string target_prefix was used by early Argos configs.
        if isinstance(value, str):
            value = [value] if value else []
        if not isinstance(value, list) or len(value) > 8 or any(
                not isinstance(token, str) or not token or len(token) > 100 for token in value):
            raise LocalTranslationError("离线模型 " + name + " 必须为短token列表。")
        return list(value)

    def describe(self, source_language, target_language, text=""):
        """Capture a versioned local configuration; no inference or networking."""
        source = self._direction(source_language, target_language, text)
        reverse = {}
        for original, translated in EXACT_TERMS.items():
            reverse.setdefault(translated.casefold(), original)
        glossary = EXACT_TERMS if source == "en" and target_language == "zh-CN" else reverse if source == "zh-CN" and target_language == "en" else {}
        term = text.strip().casefold()
        if term in glossary:
            fingerprint = hashlib.sha256(json.dumps([ADAPTER_VERSION, EXACT_TERMS], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            return dict(provider="project_glossary_local", model="medical-ml-glossary-v1", model_version="medical-ml-glossary-v1",
                        fingerprint=fingerprint, source_language=source, target_language=target_language,
                        glossary_translation=glossary[term])
        path = self.root / "configs/translation.json"
        try:
            config = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise LocalTranslationError("离线翻译尚未配置；可显式选择 Agent 校订。") from exc
        if not isinstance(config, dict) or config.get("backend") != "ctranslate2" or config.get("enabled", True) is not True:
            raise LocalTranslationError("离线翻译未启用或后端配置不受支持。")
        entries = config.get("models")
        if not isinstance(entries, list):
            raise LocalTranslationError("离线翻译缺少 models 配置。")
        matches = [entry for entry in entries if isinstance(entry, dict) and
                   entry.get("source_language") == source and entry.get("target_language") == target_language]
        if len(matches) != 1:
            raise LocalTranslationError("此语言方向未唯一配置离线模型；可显式选择 Agent 校订。")
        model = matches[0]
        version = model.get("model_version")
        if not isinstance(version, str) or not version.strip() or len(version) > 200:
            raise LocalTranslationError("离线模型缺少明确版本标识。")
        folder = self._path(model.get("model_dir"), directory=True)
        source_spm = self._path(model.get("tokenizer_path"))
        target_spm = self._path(model.get("target_tokenizer_path", model.get("tokenizer_path")))
        if not (folder / "model.bin").is_file():
            raise LocalTranslationError("离线 CTranslate2 模型缺少 model.bin。")
        compute_type = model.get("compute_type", config.get("compute_type", "int8"))
        threads = model.get("intra_threads", config.get("intra_threads", 4))
        beam = model.get("beam_size", config.get("beam_size", 2))
        if compute_type not in {"int8", "int8_float32", "float32", "default"} or type(threads) is not int or not 1 <= threads <= 16 or type(beam) is not int or not 1 <= beam <= 8:
            raise LocalTranslationError("离线翻译的CPU精度、线程或beam参数无效。")
        target_prefix = self._tokens(model.get("target_prefix", []), "target_prefix")
        source_prefix = self._tokens(model.get("source_prefix", []), "source_prefix")
        source_suffix = self._tokens(model.get("source_suffix", []), "source_suffix")
        strip_output_prefix = self._tokens(model.get("strip_output_prefix", target_prefix), "strip_output_prefix")
        files = {source_spm, target_spm}
        for file in folder.iterdir():
            if file.is_file():
                resolved = file.resolve()
                if not resolved.is_relative_to(self.root):
                    raise LocalTranslationError("离线模型文件不能通过链接越出工作区。")
                files.add(resolved)
        if len(files) > 64:
            raise LocalTranslationError("离线模型文件数量异常。")
        artifacts = [{"path": str(file), "sha256": self._hash(file)} for file in sorted(files)]
        runtimes = {}
        for package in ("ctranslate2", "sentencepiece"):
            try:
                runtimes[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                runtimes[package] = "unavailable"
        descriptor = dict(provider=self.name, model=version, model_version=version, adapter_version=ADAPTER_VERSION,
                          source_language=source, target_language=target_language,
                          model_dir=str(folder), tokenizer_path=str(source_spm), target_tokenizer_path=str(target_spm),
                          compute_type=compute_type, intra_threads=threads, beam_size=beam,
                          target_prefix=target_prefix, source_prefix=source_prefix, source_suffix=source_suffix,
                          strip_output_prefix=strip_output_prefix, artifacts=artifacts, runtimes=runtimes)
        runtime = {key: descriptor[key] for key in ("model_dir", "tokenizer_path", "target_tokenizer_path",
                   "compute_type", "intra_threads", "artifacts", "runtimes")}
        descriptor["runtime_key"] = hashlib.sha256(json.dumps(runtime, sort_keys=True).encode()).hexdigest()
        descriptor["fingerprint"] = hashlib.sha256(json.dumps(descriptor, sort_keys=True).encode()).hexdigest()
        return descriptor

    def _load(self, descriptor):
        key = descriptor["runtime_key"]
        with self._lock:
            if key not in self._models:
                for artifact in descriptor["artifacts"]:
                    if self._hash(self._path(artifact["path"])) != artifact["sha256"]:
                        raise LocalTranslationError("离线模型在排队后发生改变，请重新翻译。")
                try:
                    import ctranslate2
                    import sentencepiece
                except ImportError as exc:
                    raise LocalTranslationError("离线翻译运行库尚未安装；可显式选择 Agent 校订。") from exc
                try:
                    translator = ctranslate2.Translator(descriptor["model_dir"], device="cpu",
                        compute_type=descriptor["compute_type"], inter_threads=1, intra_threads=descriptor["intra_threads"])
                    source = sentencepiece.SentencePieceProcessor(model_file=descriptor["tokenizer_path"])
                    target = sentencepiece.SentencePieceProcessor(model_file=descriptor["target_tokenizer_path"])
                except Exception as exc:
                    raise LocalTranslationError("离线模型或分词文件无法加载，请检查部署或显式请求 Agent 校订。") from exc
                if len(self._models) >= 4:
                    self._models.pop(next(iter(self._models)))
                self._models[key] = translator, source, target
            return self._models[key]

    @staticmethod
    def _check(cancel_event):
        if cancel_event and cancel_event.is_set():
            raise LocalTranslationCancelled("离线翻译已取消。")

    def translate(self, text, descriptor, cancel_event=None):
        self._check(cancel_event)
        if not text.strip() or not re.search(r"[A-Za-z\u3400-\u9fff]",text):
            return text
        if "glossary_translation" in descriptor:
            return re.match(r"^\s*", text).group() + descriptor["glossary_translation"] + re.search(r"\s*$", text).group()
        translator, source, target = self._load(descriptor)
        parts = []
        # Bounded chunks keep cancellation responsive and disable CT2's silent
        # input truncation. Paragraph separators and protected spans stay outside.
        for paragraph in re.split(r"(\n+)", text):
            if not paragraph.strip():
                parts.append(paragraph)
                continue
            remaining = paragraph
            while remaining:
                self._check(cancel_event)
                end = min(len(remaining), 600)
                if end < len(remaining):
                    cut = max(remaining.rfind(" ", 0, end), remaining.rfind("。", 0, end), remaining.rfind(". ", 0, end))
                    if cut > 100:
                        end = cut + 1
                piece = remaining[:end]
                if not piece.strip():
                    parts.append(piece)
                    remaining = remaining[end:]
                    continue
                # NLLB's published training input limit is 512 tokens. A margin
                # also covers model-side BOS/EOS. Validate *encoded* input,
                # including explicitly configured language/EOS tokens.
                tokens = descriptor["source_prefix"] + source.encode(piece.strip(), out_type=str) + descriptor["source_suffix"]
                while len(tokens) > 480:
                    if end <= 1:
                        raise LocalTranslationError("一个字符的分词结果超过离线输入上限，请显式选择 Agent 校订。")
                    end = max(1, int(end * .8))
                    piece = remaining[:end]
                    tokens = descriptor["source_prefix"] + source.encode(piece.strip(), out_type=str) + descriptor["source_suffix"]
                remaining = remaining[end:]
                leading = re.match(r"^\s*", piece).group()
                trailing = re.search(r"\s*$", piece).group()
                prefix = [descriptor["target_prefix"]] if descriptor["target_prefix"] else None
                batches = translator.translate_batch([tokens], target_prefix=prefix, beam_size=descriptor["beam_size"],
                    max_input_length=0, max_decoding_length=1024, replace_unknowns=True)
                self._check(cancel_event)
                if not batches or not batches[0].hypotheses or not batches[0].hypotheses[0]:
                    raise LocalTranslationError("离线模型未返回译文；可显式选择 Agent 校订。")
                if len(batches[0].hypotheses[0]) >= 1024:
                    raise LocalTranslationError("离线译文达到长度上限，请缩小选区或显式请求 Agent 校订。")
                output_tokens = list(batches[0].hypotheses[0])
                strip = descriptor["strip_output_prefix"]
                if strip and output_tokens[:len(strip)] == strip:
                    output_tokens = output_tokens[len(strip):]
                output = target.decode(output_tokens).replace("▁", " ")
                parts.append(leading + output.strip() + trailing)
        return "".join(parts)
