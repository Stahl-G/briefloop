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
