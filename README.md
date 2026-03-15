[中文](#中文) | [English](#english)

---

## 中文

将 Apple 开发者文档（如 developer.apple.com/documentation）爬取为本地 Markdown 并下载图片等资源，并可选用 OpenAI 或 DeepSeek 进行翻译（如英译中）。

### 功能特点

- **爬取与转换**：从给定起始 URL 起沿链接爬取，用 Playwright 渲染页面，提取正文并保存为带 YAML front matter（title、url）的 Markdown。
- **本地资源**：下载页面中引用的图片，并将链接改写为本地路径。
- **断点续爬**：已出现在 `manifest.json` 且对应输出文件存在的页面会跳过；默认不限制页数。
- **翻译**：提供批量翻译 Markdown 的脚本（OpenAI 或 DeepSeek），支持词表。

### 环境要求

- Python 3.10+
- [Playwright](https://playwright.dev/python/)（Chromium）

```bash
pip install beautifulsoup4 markdownify playwright
playwright install chromium
```

翻译为可选，需额外依赖：
- `translator-openai.py`：`openai` 与 `OPENAI_API_KEY`
- `translator-deepseek.py`：`openai`（兼容 DeepSeek）+ `DEEPSEEK_API_KEY`

### 使用方法

#### 1. 下载文档

```bash
python main.py --url "https://developer.apple.com/documentation/uikit"
```

- **`--url`**（必填）：爬取起始 URL（如某个框架或主题根路径）。
- **`--out`**：输出目录；不填则根据 URL 路径推导（如 `uikit`）。
- **`--max-pages`**：最多爬取页数（默认不限制）。
- **`--concurrency`**：并发浏览器标签页数量（默认 8）。
- **`--wait-ms`**：页面加载后等待多少毫秒再提取内容（默认 500）。
- **`--strict-asset-ssl`**：下载图片时强制校验 SSL（默认允许不校验的回退）。

自定义输出目录并限制页数示例：

```bash
python main.py --url "https://developer.apple.com/documentation/foundation" --out ./foundation-docs --max-pages 500
```

输出结构：
- 输出目录下每页一个 `.md` 文件。
- `manifest.json` 记录标题、url、文件名。
- `assets/<页面 stem>/` 下为该页图片。

#### 2. 翻译（可选）

本项目提供两个翻译脚本，将下载得到的英文 Markdown 批量翻译为简体中文，并支持通过**词表（Glossary）**统一术语。

| 脚本 | 后端 | 环境变量 |
|------|------|----------|
| `translator-openai.py` | OpenAI（如 gpt-4o-mini） | `OPENAI_API_KEY` |
| `translator-deepseek.py` | DeepSeek API（兼容 OpenAI 接口） | `DEEPSEEK_API_KEY` |

**基本用法：**

**OpenAI：**
```bash
export OPENAI_API_KEY="your-key"
python translator-openai.py --folder ./uikit --out ./uikit/translated
```

**DeepSeek：**
```bash
export DEEPSEEK_API_KEY="your-key"
python translator-deepseek.py --folder ./uikit --out ./uikit/translated
```

- **`--folder`**（必填）：待翻译的 Markdown 所在目录（即 `main.py` 的 `--out` 输出目录或其子目录）。
- **`--out`**：翻译结果输出目录；不填则默认为 `<folder>/translated`。
- **`--glossary`**：词表文件路径；不填则使用当前工作目录下的 `glossary.md`（若存在）。

已存在于输出目录中的文件会被跳过，便于断点续译。

---

**词表（Glossary）说明**

词表用于在翻译时固定专有术语的译法（如 Swift/UIKit/SwiftUI 等），减少同一术语多种译法。仓库内自带 [glossary.md](glossary.md)，面向 Apple 开发者文档、Swift、SwiftUI 等场景。

**格式**：词表为 Markdown 文件，内含表格；表格需包含 **Term** 列与 **Suggest Transition**（或 **Translation**）列，一行一条术语约定。例如：

```markdown
| Term              | Suggest Transition |
| ----------------- | ------------------ |
| closure           | 闭包               |
| property          | 属性               |
| view modifier     | 视图修饰器         |
| NavigationStack   | 导航栈             |
```

**使用方式**：
- 不指定 `--glossary` 时，脚本会尝试读取当前目录下的 `glossary.md`。
- 指定词表：`--glossary /path/to/glossary.md`。
- 不使用词表：不提供该文件或传空文件即可。

翻译时模型会将词表作为 system 提示的一部分，按表中约定优先采用对应译法。

### 开源协议

本项目采用 **MIT 许可证** 发布，详见 [LICENSE](LICENSE)。

### 免责声明

本工具仅供个人与学习使用。请遵守 Apple 服务条款与站点规则，勿对站点造成过大压力。

---

## English

Download Apple Developer documentation (e.g. developer.apple.com/documentation) as local Markdown with assets. Optionally translate the content (e.g. EN → Chinese) via OpenAI or DeepSeek.

### Features

- **Crawl & convert**: Follows links under a given start URL, renders pages with Playwright, extracts main content, and saves as Markdown with YAML front matter (title, url).
- **Local assets**: Downloads images referenced in pages and rewrites links to local paths.
- **Resume-friendly**: Skips pages already in `manifest.json` with existing output file; no page limit by default.
- **Translation**: Scripts to translate Markdown in batch (OpenAI or DeepSeek) with glossary support.

### Requirements

- Python 3.10+
- [Playwright](https://playwright.dev/python/) (Chromium)

```bash
pip install beautifulsoup4 markdownify playwright
playwright install chromium
```

Optional for translation:
- `translator-openai.py`: `openai` + `OPENAI_API_KEY`
- `translator-deepseek.py`: `openai` (DeepSeek-compatible client) + `DEEPSEEK_API_KEY`

### Usage

#### 1. Download documentation

```bash
python main.py --url "https://developer.apple.com/documentation/uikit"
```

- **`--url`** (required): Start URL to crawl (e.g. a framework or topic root).
- **`--out`**: Output directory; default is derived from the URL path (e.g. `uikit`).
- **`--max-pages`**: Cap number of pages (default: no limit).
- **`--concurrency`**: Number of concurrent browser tabs (default: 8).
- **`--wait-ms`**: Milliseconds to wait after page load before extracting (default: 500).
- **`--strict-asset-ssl`**: Enforce strict SSL when downloading images (default: allow insecure fallback).

Example with a custom output dir and page cap:

```bash
python main.py --url "https://developer.apple.com/documentation/foundation" --out ./foundation-docs --max-pages 500
```

Output layout:
- One `.md` file per page under the output directory.
- `manifest.json` listing title, url, and filename.
- `assets/<page_stem>/` for images of each page.

#### 2. Translate (optional)

Two translator scripts batch-translate the downloaded English Markdown into Simplified Chinese, with optional **Glossary** support for consistent terminology.

| Script | Backend | Env var |
|--------|---------|---------|
| `translator-openai.py` | OpenAI (e.g. gpt-4o-mini) | `OPENAI_API_KEY` |
| `translator-deepseek.py` | DeepSeek API (OpenAI-compatible) | `DEEPSEEK_API_KEY` |

**Basic usage:**

**OpenAI:**
```bash
export OPENAI_API_KEY="your-key"
python translator-openai.py --folder ./uikit --out ./uikit/translated
```

**DeepSeek:**
```bash
export DEEPSEEK_API_KEY="your-key"
python translator-deepseek.py --folder ./uikit --out ./uikit/translated
```

- **`--folder`** (required): Directory containing the Markdown files to translate (typically the `--out` directory from `main.py` or a subdirectory).
- **`--out`**: Output directory for translated files; default is `<folder>/translated`.
- **`--glossary`**: Path to the glossary file; if omitted, the script uses `glossary.md` in the current working directory when present.

Files that already exist in the output directory are skipped, so you can resume interrupted runs.

---

**Glossary**

The glossary fixes how technical terms (e.g. Swift, UIKit, SwiftUI) are translated so the same term is not rendered in multiple ways. The repo includes a sample [glossary.md](glossary.md) aimed at Apple Developer Documentation / Swift / SwiftUI.

**Format:** The glossary is a Markdown file with one or more tables. Each table must have a **Term** column and a **Suggest Transition** (or **Translation**) column, one row per term. Example:

```markdown
| Term              | Suggest Transition |
| ----------------- | ------------------ |
| closure           | 闭包               |
| property          | 属性               |
| view modifier     | 视图修饰器         |
| NavigationStack   | 导航栈             |
```

**Usage:**
- If you do not pass `--glossary`, the script looks for `glossary.md` in the current working directory.
- To use a custom file: `--glossary /path/to/glossary.md`.
- To run without a glossary: omit the file or pass an empty one.

The script injects the glossary into the model’s system prompt so the model prefers the given translations for listed terms.

### License

This project is released under the **MIT License**. See [LICENSE](LICENSE) for the full text.

### Disclaimer

This tool is for personal and educational use. Respect Apple's [Terms of Use](https://www.apple.com/legal/internet-services/terms/site.html) and robots.txt; avoid excessive request rates.
