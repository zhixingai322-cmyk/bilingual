# Paper Bilingual PDF · 论文中英对照翻译

供 ChatGPT / Codex 调用的翻译技能与离线排版工具。中文一段，后接对应英文一段；原文正文重新排版，公式重新渲染，图片完整保留，参考文献只保留英文。

**2.0 修复了英文整体放大导致字号过大的问题。** 中英文正文均为 **11 pt 字号、16 pt 行距、44 pt 左右边距**，标题、图注等也采用对应的统一样式。排版使用真实可选择文字，不再把英文整栏截图放大到正文宽度。

翻译由调用技能的助手完成；CLI 负责结构、检查和排版。无需单独的翻译 API Key，不上传论文到外部翻译网站。

## 安装和使用

Python 3.10+：

```bash
python -m pip install .
paper-bilingual --help
```

依赖 PyMuPDF、ReportLab、Matplotlib；不需要额外安装 TeX。将本项目作为技能导入后，可以直接说：

> 调用 translate-paper-bilingual，将这篇英语 PDF 论文完整翻译为中英对照 PDF。中英文统一格式，数学公式重新渲染，完整保留图片与图注，参考文献保留英文，并逐页检查。

## 默认排版

- 中英文正文：11 pt / 16 pt，统一颜色、正文宽度与段落样式。
- 中英文标题：同级对应字号；章标题与后续段落保持在同页。
- 短对照段落：中文在前，原始英文在后，整对保持在同一页。
- 数学公式：LaTeX 风格 MathText 数学渲染，显示公式为矢量 PDF，原编号置于右侧；分式、上下标、积分、求和、希腊字母等正常排版。
- 图片：直接嵌入原始 PDF 图形区域，保留子图、坐标轴和标签，与双语图注成组。带批注/表单的源页使用包含可见批注的 240 dpi 图像以保留外观。
- 参考文献：仅英文，不翻译作者或文献题名。
- 页面：固定 A4，克制的页眉页脚、嵌入字体；中文标点使用完整的 CJK 字形。

添加中文和重新排版会改变行数和页码。图片与公式保持相对于上下文的内容顺序，不能同时保持原始绝对页坐标。

## 工作流

```bash
paper-bilingual prepare paper.pdf work/paper
# 查看所有 layout 页，修正列顺序及图片/公式分组。
# 在 manifest.json 的 blocks 中逐段填写英文、译文、公式与图注。
paper-bilingual check work/paper
paper-bilingual approve work/paper --layout --translation
paper-bilingual render work/paper bilingual.pdf
paper-bilingual proof bilingual.pdf work/proof
```

旧版任务先执行：

```bash
paper-bilingual typeset work/old-paper
```

若修改源区域矩形或顺序，执行 `refresh JOB`，然后重新审阅变化区域。`refresh` 保留未变化片段并清除变化片段的译文/结构。`render --draft` 只用于内部检查，不代表翻译完成。

## 结构化内容示例

每个源区域有有序的 `blocks`，可以按方便对照的原则划分为多个短段落：

```json
[
  {"type":"text","style":"heading","zh":"2. 模型","en":"2. Model"},
  {"type":"text","zh":"时延 $\\tau$ 决定响应。","en":"The delay $\\tau$ determines the response."},
  {"type":"equation","bbox":[42,200,290,235],"latex":"R(t)=\\frac{1}{1+t^2}","number":"1"},
  {"type":"figure","bbox":[42,490,570,675],"zh_caption":"图 1. 系统响应。","en_caption":"Figure 1. System response."},
  {"type":"references","style":"heading","en":"References"},
  {"type":"references","en":"[1] A. Author. Original title. Journal, 2026."}
]
```

公式/图片 bbox 为原 PDF 中的区域，坐标从左上角起，单位为 PDF 点；正文不从截图放大。完整字段与扫描件处理见 [操作规范](references/workflow.md)。

## 完整性检查

保留不可变原始 PDF 和完整、不重叠的原页区域划分。重新排版前核对：

- 英文正文、标题、图注与源文一致，不能缺段或改变顺序。
- 每个原始图形都由图片/公式区域明确承接。
- 正文必须有中文，参考文献必须无中文译文。
- 图片、数学转写和扫描内容已逐页核对。
- 修改内容会使旧审核摘要失效，正式输出要求重新审核。

几何和字符串检查不能替代翻译及数学含义的视觉核对。自动分段只是草案，复杂多栏、多子图、跨页续句和扫描页仍由助手审阅。重复页眉/页脚/页码可在边缘小区域中明确记录后省略，不能用此机制删掉正文。

## 数学支持范围

数学排版采用 MathText 的 TeX 数学语法子集，不是完整 LaTeX 文档编译器。支持常用分式、根号、求和、积分、希腊字母、重音及上下标；正文中用 `$...$` 插入行内公式，`latex` 字段不含外层美元符号。行内公式以匹配字号的高分辨率图像嵌入，显示公式保持矢量。

不支持的自定义宏或环境会明确报错，不会把原始 TeX 当公式打印，也不静默回退为低清截图。多行推导可拆成连续短公式块，保持原次序及编号；复杂矩阵或自定义宏需要先解决数学表示再交付。不执行外部 TeX、shell 或来源文档中的命令。

## 验证

```bash
python -m pip install '.[test]'
python scripts/selftest.py
```

测试使用自行生成的合成论文。覆盖统一字号/行距/边距、公式渲染与编号位置、中文标点、标题分页、英文完整性、图形承接、英文参考文献、扫描审阅、来源哈希、几何覆盖与审核失效。旧版的原文图形保留路径仍有回归测试，但不再作为默认正文排版。

不提交用户论文、译稿、密钥或中间文件。适用的依赖许可证请参见各依赖项目。
