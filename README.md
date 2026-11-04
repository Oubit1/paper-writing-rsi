<div align="center">

# ✍️ Paper Atelier

### 让每一次讨论，落到论文中真正需要修改的地方。

**PDF 原稿交互 · 多角色协作 · 精确局部修订 · 持续写作记忆**

[![Checks](https://github.com/Oubit1/paper-writing-rsi/actions/workflows/check.yml/badge.svg)](https://github.com/Oubit1/paper-writing-rsi/actions/workflows/check.yml)
![Python](https://img.shields.io/badge/Python-3.10–3.13-3776AB?logo=python&logoColor=white)
![Windows](https://img.shields.io/badge/Windows-x64-0078D4)
![Translation](https://img.shields.io/badge/Translation-Local_INT8-15803d)

[观看演示](#观看演示) · [核心体验](#核心体验) · [写作闭环](#写作闭环) · [快速开始](#快速开始) · [写作技能](#写作技能)

</div>

[![Paper Atelier 工作流演示](docs/assets/workflow-preview.gif)](https://github.com/Oubit1/paper-writing-rsi/blob/main/docs/assets/workflow-demo.mp4)

<p align="center"><b>打开论文，选中问题，和 Agent 一起修改。</b><br>点击动图观看完整录屏。</p>

## 为什么做这个工作台？

一篇论文的修改往往穿梭于 PDF、LaTeX、聊天窗口和审稿意见之间。讨论积累了很多，落到原稿时，却仍要重新定位、核对上下文、判断哪些建议值得接受。

Paper Atelier 把这些动作放进同一个工作区：**以当前稿件为中心，让章节作者、讨论教师和独立审阅者围绕同一份原文协作。** 从一个词、一句话或一个公式开始修改，也可以回到整篇论文讨论创新点与论证结构。讨论记录、候选修改和已接受正文各有明确的位置。

## 核心体验

| 你想做的事 | 工作台如何支持 |
| --- | --- |
| **📦 带着现有论文开始** | 上传 LaTeX ZIP，自动解压、识别主文件、加载章节与素材并编译 PDF；多入口工程可选择主文件。 |
| **📖 对照排版读稿** | 阅读真实编译 PDF，缩放、翻页和选择文本；章节写作与审阅展示对应原文，公式和表格保留排版。 |
| **🎯 精改具体问题** | 建议指向词、短语、句子或完整公式，给出修改理由与差异；逐项接受或拒绝，保留版本与撤销/重做。 |
| **🤝 按任务协作** | Introduction、Method、Related Work、实验与科研绘图各有角色；创新讨论、全局讨论、独立审阅和校准承担不同任务。 |
| **🌐 划词即译** | 本地中英双向 INT8 模型提供快译，需要时显式调用 Agent 校订；术语检查与反馈支持后续评估。 |
| **🧠 让反馈延续到下一轮** | 保留课题讨论、作者规则和经审阅的项目经验；后续任务回报规则符合情况与冲突。 |

## 观看演示

[![PDF 原稿与选区交互](docs/assets/pdf-workbench.jpg)](docs/assets/pdf-workbench.jpg)

**[▶ 完整工作流录屏](https://github.com/Oubit1/paper-writing-rsi/blob/main/docs/assets/workflow-demo.mp4)**：章节对照阅读 → PDF 选区与翻译 → Agent 交互 → 协作任务切换。

<sub>录屏由项目作者提供，展示实际部署界面。发布版本压缩画面并移除音轨与源元数据；录屏中的论文和既有任务用于界面展示，新安装从空项目开始。</sub>

## 写作闭环

```mermaid
flowchart LR
    A[导入论文与资料] --> B[阅读原稿 / 定位问题]
    B --> C[章节写作与教师讨论]
    C --> D[局部修改建议]
    D --> E[独立审阅与校准]
    E --> F{作者选择}
    F -->|接受| G[更新正文与 PDF]
    F -->|继续讨论| C
    G --> H[关联检查与项目经验审阅]
    H --> B
```

这里的 RSI 聚焦**写作规则、上下文与经审阅经验的复用**。一次作者反馈可以成为后续修改的约束；一条未核实或存在冲突的要求，也应被明确留下，而不是随着对话增长消失。

| 环节 | 保留下来的内容 |
| --- | --- |
| 讨论与章节写作 | 角色记录、原文上下文、候选内容和证据缺口 |
| 作者接受修改 | 实际修改差异、来源版本与修订历史 |
| 独立审阅与校准 | 具体问题、依据、适用范围和待核实项 |
| 经验复用 | 经审阅的项目经验、作者规则检查和冲突说明 |

这是写作流程的迭代机制，不代表模型权重训练；角色意见一致也不构成研究结论或论文质量提升的证明。

## 快速开始

**Windows x64 · Python 3.10–3.13 · 已登录的官方 Codex。**

```powershell
git clone https://github.com/Oubit1/paper-writing-rsi.git
cd paper-writing-rsi
.\Install.ps1
.\Start.ps1
```

也可下载源码 ZIP，解压后双击 **安装依赖.cmd**，完成后双击 **启动论文工作台.cmd**。

1. 在自己的 Codex 中登录，在工作台模型设置中选择账号实际可用的模型。
2. 打开 [本地工作台](http://127.0.0.1:8766/)，点击 **上传 ZIP** 导入论文工程。
3. 等待 PDF 编译，选择一个章节或一段原文，发起讨论或局部修改。
4. 对照建议，选择接受或继续讨论，再更新 PDF。

首次安装联网下载依赖、Tectonic、翻译模型与固定版本的写作 Skill；首次编译可能下载 TeX 包。后续划词快译在本地运行，写作和 Agent 校订使用自己的 Codex 登录。

ZIP 上限 **64 MB**，解压上限 **256 MB / 500 个文件**；TeX 使用 UTF-8，需附带图表与文献资源。支持展开 `\input` / `\include`，每次上传创建独立课题。

停止服务运行 `.\Stop.ps1`；端口冲突使用 `.\Start.ps1 -Port 8767`。若 Codex 无法定位，可设置 `PAPER_AGENT_CODEX` 指向本机可执行文件。

## 写作技能

已接入 **[MLNLP Paper-Writing-Tips](https://github.com/MLNLP-World/Paper-Writing-Tips)**，覆盖公式符号、LaTeX、图表、表达、引用与终稿检查。章节写作加载核心规则，独立审阅与校准额外加载终稿清单。

社区建议按当前模板、作者要求和原文含义适用。Skill 注入范围与文件哈希可追溯；规则改变后，源码编辑请求不复用旧规则生成的缓存。安装器从固定上游版本获取原文并核验 SHA256，详见 [接入说明](docs/WRITING_SKILLS.md)。个人范本、导师偏好及项目专属材料可在自己的部署中维护。

## 文献与数据

稿件保存在 `projects/`，修订与任务记录保存在 `runtime/`，资料保存在 `library/`。公共仓库提供源码与通用配置，不包含账号、私人稿件、运行数据库、模型权重或完整分享包。

Crossref 关键词补充默认关闭。使用者同意后，可在 `configs/library_enrichment.json` 启用 `crossref_public_topics`，获取相关文献元数据及可用摘要。模型写作请求会使用任务所需的稿件上下文；本地工作台并不表示所有推理都离线。

<details>
<summary><b>遇到问题？</b></summary>

| 问题 | 先检查 |
| --- | --- |
| 无法启动 | 完成依赖安装，确认目录可写，查看 `runtime/server.err.log`。 |
| 模型不可用 | 检查 Codex 登录和角色模型配置；预设型号不代表账号权限。 |
| 翻译 DLL 缺失 | 安装微软 Visual C++ x64 运行库。 |
| PDF 编译失败 | 查看诊断，确认图片、样式与文献文件齐全，首次编译网络可用。 |

不要让两台机器同时写入同一份数据库；共享源码时不要直接复制已经使用过的运行目录。

</details>

## 开发与验证

| 目录 | 职责 |
| --- | --- |
| `app/` | 工作流、原稿修订、编译、翻译与记录 |
| `static/` | PDF 编辑器、协作界面与知识库 |
| `agents/`、`configs/` | 角色指引和模型/工作流配置 |
| `scripts/`、`tests/` | 组件安装与隔离回归检查 |

[GitHub Actions](https://github.com/Oubit1/paper-writing-rsi/actions/workflows/check.yml) 检查源码、前端脚本、ZIP 导入、PDF 资源快照及 Skill 接入等行为。软件测试不替代翻译准确性评测或学术质量评价。

## 相关项目与致谢

- [CMR/LGE RSI](https://github.com/Oubit1/cmr-lge-rsi)：数据标注质量控制的配套项目；本项目独立运行。
- [FlashRSI](https://github.com/RUC-NLPIR/FlashRSI)：Agent 自我改进实验框架，也是本页组织方式的参考。
- [Paper-Writing-Tips](https://github.com/MLNLP-World/Paper-Writing-Tips)：已接入的社区写作检查 Skill。

第三方来源与许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和 [licenses/](licenses/)。项目自身尚未指定开源许可证。

欢迎通过 [Issues](https://github.com/Oubit1/paper-writing-rsi/issues) 提交可复现问题与工作流建议。
