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

将某目录下全部 Markdown 翻译到例如 `translated/`：

**OpenAI（如 gpt-4o-mini）：**
```bash
export OPENAI_API_KEY="your-key"
python translator-openai.py --input ./uikit --output ./translated
```

**DeepSeek：**
```bash
export DEEPSEEK_API_KEY="your-key"
python translator-deepseek.py --input ./uikit --output ./translated
```

分块大小、词表、模型等见各脚本的 `--help`。

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

Translate all Markdown under a directory, e.g. into `translated/`:

**OpenAI (e.g. gpt-4o-mini):**
```bash
export OPENAI_API_KEY="your-key"
python translator-openai.py --input ./uikit --output ./translated
```

**DeepSeek:**
```bash
export DEEPSEEK_API_KEY="your-key"
python translator-deepseek.py --input ./uikit --output ./translated
```

See each script's `--help` for chunk size, glossary, and model options.

### License

This project is released under the **MIT License**. See [LICENSE](LICENSE) for the full text.

### Disclaimer

This tool is for personal and educational use. Respect Apple's [Terms of Use](https://www.apple.com/legal/internet-services/terms/site.html) and robots.txt; avoid excessive request rates.
