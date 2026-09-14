# Pinned official scorer — do not edit this directory

`reward.py` is a byte-exact copy of the official Databricks OfficeQA reward
function at the commit pinned by protocol BL-OQA-SR-v1.0 §1.3:

- repository: `databricks/officeqa`
- commit: `7b9a3c154ef9fb40215bb67934afc43e6799de16`
- Git blob SHA-1: `45a22db4771f20105bdc9340df82e3c405ec84d0`
- SHA-256: `0d91698c87df6d889339aac36f63ae0966607f169890b0bf8b472b26bfe8138f`

The file carries **no added header comment** on purpose: any local edit
changes both hashes and breaks the pin. Both hashes are double-recorded in
`../config.json` and re-verified by `tests/test_officeqa_score_projection.py`
and `score_answer_record.verify_scorer_pin()`.

Upstream license (repo root at the pinned commit): code and scripts are
Apache 2.0 (`LICENSE-APACHE`); datasets are CC-BY-SA 4.0
(`LICENSE-CC-BY-SA`). This copy is code, distributed under Apache-2.0,
(c) Databricks. See `../SCORER_VERIFICATION.md` for the full provenance and
the comparison against the vendored `src/wikiskill/officeqa/reward.py`.
