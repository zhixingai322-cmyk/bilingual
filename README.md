# Paper Bilingual PDF · 论文中英对照翻译

供 ChatGPT / Codex 使用的翻译技能和离线 PDF 排版工具。每段中文在前，后接相应英文原文；原文以 PDF 内容片段直接嵌入，完整保留公式、图表、编号和字体。按照方便对照的原则分段，不强制沿用原论文的自然段。

**翻译由调用本技能的助手完成。** CLI 负责分段、检查和排版，不内置翻译模型，不需要额外的翻译 API Key，也不会把论文上传到外部翻译网站。

## 安装与调用

在 Python 3.10+ 环境安装 CLI：

```bash
python -m pip install .
paper-bilingual --help
```

将本项目作为 Codex 技能安装后，可直接提出：

> 使用 translate-paper-bilingual 将这篇英语 PDF 论文完整翻译为中英对照 PDF。中文一段，后接对应英文；完整保留公式、图片、图注和参考文献，并逐页检查。

`SKILL.md` 是助手的完整操作规程。技能包包含 `scripts/`、`references/` 和 `agents/`，可通过支持 GitHub 技能导入的客户端安装本项目。ChatGPT 的个人技能应通过其技能目录安装；仅复制到任意文件夹不代表已经安装。

## 实现方式

1. 从原文生成可审阅的分段草案，兼顾双栏、跨栏内容与段落边界。
2. 对每个片段填写中文翻译。公式附近的说明、图注、图片内部英文标签均需译出。
3. 检查每一页是否被完整覆盖、是否重复、是否切断文字行、图片或检测到的矢量图形。
4. 审核版面和译文后，按“中文 → 原文片段”生成 PDF。
5. 渲染全部输出页，核对顺序、字形、图表与公式完整性。

常规原文保留矢量内容和可选择文字；带批注/表单的页面使用含可见批注的 240 dpi 图像，防止可见内容丢失。中文嵌入字体。英文片段去除纯白页边与栏间留白后，等比例铺满与中文相同的正文宽度，统一左右 36 pt 边距。同一原页和栏位共享横向裁切范围，保留短标题、公式与正文的原有相对位置。整组中文和对应原文保持在同一输出页；大图不缩小到低于原始物理尺寸，必要时增加纸张高度。

“保持原位”指片段内部的原始位置及论文阅读顺序。添加中文会增加篇幅和页数，不能同时维持原始页码和绝对坐标。

## 命令行工作流

```bash
paper-bilingual prepare paper.pdf work/paper
# 查看 layout.pdf / layout-*.png；必要时编辑 manifest.json 的有序矩形片段。
paper-bilingual refresh work/paper  # 仅在修改分段或顺序后调用
# 助手在 manifest.json 各片段 zh 字段中完成对应翻译。
paper-bilingual check work/paper
paper-bilingual approve work/paper --layout --translation
paper-bilingual render work/paper bilingual.pdf
paper-bilingual proof bilingual.pdf work/proof
```

- `prepare --layout auto|single|double`：自动、单栏或双栏草案。
- `--target-height 160`：目标片段高度，单位为 PDF 点；有完整图表时允许超出。
- `render --draft`：仅生成内部检查用草稿；草稿不代表翻译完成。
- `check --require-review`：同时检查审核摘要是否有效。
- `refresh`：重新提取内容，清除变化片段的译文并取消审核；保留未变化片段的译文。
- 输出 `.audit.json`：记录来源哈希、完整分段坐标、去白边后的显示裁切范围、缩放比例和输出位置，便于追溯。

## 扫描件和复杂版面

自动分段是草案，不能替代逐页视觉审阅。特别检查多子图、断开的矢量图、图注、跨栏图片和页间续句。分组不当时调整矩形并 `refresh`，几何检查不通过则禁止正式输出。

扫描件不假装已完成 OCR：先视觉阅读或使用可用 OCR，填写 `ocr_text` 与 `zh`，核对后标记 `scan_checked`。纯图像区域也需视觉核对。自动检查证明的是几何完整性，翻译准确性与图内标签译全仍由助手审阅。

中文按纯文本排版，不解释 Markdown 或 LaTeX。复杂公式保留在原文片段中，中文说明可使用 Unicode 或简洁数学记号。

## 测试

```bash
python -m pip install '.[test]'
python scripts/selftest.py
```

测试完全使用自行生成的合成论文，包括双栏、公式、矢量子图、扫描页、批注、缺译、来源变更、覆盖缺口与重复、切图、失效审核、中英边界对齐、浅色图形保留和短标题统一缩放。不提交用户论文、译稿、密钥或中间文件。

本工具没有网络请求，也不执行外部命令。PyMuPDF 的适用许可证请参见其项目说明。
