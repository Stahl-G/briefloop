# MinerU Document Parsing Integration

[MinerU](https://github.com/opendatalab/MinerU) is a high-accuracy document
parsing engine that converts PDF, DOCX, PPTX, XLSX, and images into structured
Markdown/JSON.

## Prerequisites

```bash
pip install "mineru[all]"
# Or: uv pip install -U "mineru[all]"
```

Requires Python 3.10-3.13. See [MinerU docs](https://opendatalab.github.io/MinerU/) for detailed installation.

## Configuration

In `sources.yaml`:

```yaml
source_strategy:
  enabled_providers:
    - mineru

mineru:
  enabled: true
  paths:
    - name: "Q1 Report"
      path: "input/q1-report.pdf"
    - name: "Research Papers"
      path: "input/papers/"
  backend: pipeline     # pipeline (CPU-friendly) | hybrid | vlm
  output_dir: "output/mineru_output"
```

## How It Works

1. `multi-agent-brief doctor` checks that `mineru` is available in PATH.
2. When collecting sources, each configured path is parsed via `mineru -p <path> -o <output_dir> -b <backend>`.
3. The generated `.md` and `.json` files are read and converted into `SourceItem` entries.
4. These entries enter the normal brief pipeline alongside other sources.

## Supported Formats

- PDF (including scanned documents with OCR)
- DOCX (native, no conversion needed)
- PPTX
- XLSX
- Images (PNG, JPG, TIFF, etc.)

## Backend Options

| Backend | Accuracy | Requirements |
|---------|----------|-------------|
| `pipeline` | ~85 (OmniDocBench) | CPU or GPU, 4GB+ VRAM, stable |
| `hybrid` | ~95 | GPU required, 8GB+ VRAM |
| `vlm` | ~95 | GPU required, 8GB+ VRAM |

The default `pipeline` backend works on CPU with 16GB+ RAM and is recommended
for most workflows.

## Remote API Mode (no local MinerU installation)

If you don't want to install MinerU locally (it's a large PyTorch/GPU dependency),
you can use MinerU's cloud API. Two tiers are available:

| Feature | Agent Lightweight | Premium |
|---------|-------------------|---------|
| **Token required** | No (IP rate-limited) | Yes (Bearer token) |
| **File size limit** | ≤10 MB | ≤200 MB |
| **Page limit** | ≤20 pages | ≤200 pages |
| **Output** | Markdown only (CDN) | Markdown + JSON + zip |
| **Models** | Fixed pipeline | pipeline / vlm / MinerU-HTML |
| **API base** | `https://mineru.net/api/v1/agent` | `https://mineru.net/api/v4` |

Get a premium token at [mineru.net](https://mineru.net) → Personal Center → API Token.

### Agent mode (no token, easiest)

```yaml
mineru:
  enabled: true
  mode: remote
  files:
    - name: "Annual Report"
      url: "https://cdn-mineru.openxlab.org.cn/demo/example.pdf"
    - name: "Local Contract"
      path: "input/contract.pdf"          # local file → signed upload
  language: ch
```

### Premium mode (higher limits, better accuracy)

```yaml
mineru:
  enabled: true
  mode: remote
  api_type: premium
  api_token: "your_token"                # or set env MINERU_API_TOKEN
  model_version: vlm                      # pipeline | vlm | MinerU-HTML
  files:
    - name: "Large Report"
      url: "https://cdn-mineru.openxlab.org.cn/demo/example.pdf"
  language: ch
  poll_timeout: 300                        # max seconds to wait
  poll_interval: 3                         # seconds between polls
```

### How remote mode works

1. For each file entry, MinerUProvider submits a parse task to the API.
2. If `path` is used (local file), the file is uploaded to MinerU's OSS via signed URL.
3. The provider polls the task status until `state=done`.
4. The parsed Markdown is returned as a SourceItem.

