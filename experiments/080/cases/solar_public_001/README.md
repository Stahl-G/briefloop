# solar_public_001

`solar_public_001` is a public-safe MABW-080 pilot case skeleton.

It contains:

- `case_manifest.json`
- `frozen_fact_layer.json`
- `guidance_set.json`
- a synthetic seed archive under `output/runs/`
- an assessment template under `assessments/`

The seed archive exists only to provide a frozen fact layer for
`experiments 080 scaffold-condition`. It is not a completed baseline, memory, or
prompt-only condition run.

This fixture does not claim:

- output quality improvement
- guidance manifestation
- model performance
- semantic source-support verification
- a complete A/B result

Expected operator flow:

```bash
multi-agent-brief experiments 080 validate-case experiments/080/cases/solar_public_001

multi-agent-brief experiments 080 scaffold-condition \
  --case experiments/080/cases/solar_public_001 \
  --condition baseline \
  --workspace <initialized-baseline-workspace>

multi-agent-brief experiments 080 scaffold-condition \
  --case experiments/080/cases/solar_public_001 \
  --condition memory \
  --workspace <initialized-memory-workspace>

multi-agent-brief experiments 080 scaffold-condition \
  --case experiments/080/cases/solar_public_001 \
  --condition prompt_only \
  --workspace <initialized-prompt-only-workspace>
```

After each condition run completes, use `register-run`, `score-run`,
`import-assessment`, and `summarize` explicitly.
