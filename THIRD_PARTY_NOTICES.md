# Third-party components

- KaTeX: https://github.com/KaTeX/KaTeX — retained MIT notice in licenses/katex-MIT.txt.
- PDF.js: https://github.com/mozilla/pdf.js — Apache 2.0; original source notices retained, license included.
- Tectonic: https://github.com/tectonic-typesetting/tectonic — compiler binary copied unchanged; upstream MIT license included. No local TeX/font cache distributed. Tectonic contains third-party components covered by their own licenses; consult the matching upstream source distribution.
- English–Chinese and Chinese–English Argos/OPUS-MT 1.9 models: Jörg Tiedemann and Santhosh Thottingal, “OPUS-MT — Building open translation services for the World”, EAMT 2020. Original packaged README declares CC BY 4.0: https://creativecommons.org/licenses/by/4.0/ . Model files copied unchanged; original READMEs and source manifests retained in tools/translation/models/. Inference uses INT8 via CTranslate2.
- Python packages are installed separately by pip from requirements files, with their own distribution notices. Codex and Python themselves are not bundled.

No private paper corpus, author's personal writing exemplars or vendor repository snapshot is redistributed.


MLNLP-World/Paper-Writing-Tips: https://github.com/MLNLP-World/Paper-Writing-Tips . No LICENSE file observed at pinned commit 50712564062249b28b8493ebdc35f8b3a3b88512. Upstream text is not included in this source repository; the installer fetches the four Markdown files directly from the pinned upstream and verifies SHA256. Local adaptation policy and integration code are separate.
