# 官方 scorer 固定与 vendored 副本核对记录（Q0）

日期：2026-09-14。依据协议 BL-OQA-SR-v1.0 §1.3 与设计 design-0223 §3.Q0。

## 1. 下载与哈希双记

- 来源：`https://raw.githubusercontent.com/databricks/officeqa/7b9a3c154ef9fb40215bb67934afc43e6799de16/reward.py`
- 落盘：`experiments/officeqa_structured/official/reward.py`（28094 字节，未加任何本地注释）
- Git blob SHA-1（`git hash-object`）：`45a22db4771f20105bdc9340df82e3c405ec84d0` ✅ 与协议 §1.3 固定值一致
- 文件 SHA-256（`shasum -a 256`）：`0d91698c87df6d889339aac36f63ae0966607f169890b0bf8b472b26bfe8138f`
- 两个哈希均为运行时重算对象：`config.json` 的 `scorer` 块记录，`tests/test_officeqa_score_projection.py` 每次测试从实际文件重算并比对；`score_answer_record.verify_scorer_pin()` 供 runner 在评分前调用，不符即抛 `ScorerPinError`（协议 §4：基础设施错误，暂停该批评分）。
- 上游许可证（固定 commit 仓库根）：代码与脚本 Apache 2.0（`LICENSE-APACHE`），数据集 CC-BY-SA 4.0（`LICENSE-CC-BY-SA`）；README 第 26 行原文："Datasets released under **CC-BY-SA 4.0** and code and scripts under **Apache 2.0 License**."

## 2. 与 vendored `src/wikiskill/officeqa/reward.py` 的逐函数对比

方法：

1. `diff <(tail -n +5 src/wikiskill/officeqa/reward.py) official/reward.py` → 退出码 0，即去掉 vendored 文件前 4 行来源注释后**字节完全一致**。
2. Python `ast` 解析两文件，枚举全部顶层符号（函数与常量）逐一比较 AST dump：
   - 双方各 29 个顶层符号（24 个函数 + 5 个模块级常量/正则：`_CURRENCY_SYMBOLS`、`_NUMBER_BODY`、`_VALID_THOUSANDS_RE`、`_LIST_NUMBER_RE`、`_INLINE_MARKUP_RE`）；
   - 仅在官方存在的符号：无；仅在 vendored 存在的符号：无；同名但函数体/常量值不同的符号：**无**。
3. 结论：vendored 副本在代码层面与官方 blob 完全等价，唯一差异是头部 4 行 provenance 注释（`# Vendored verbatim from ... Retrieved 2026-09-04 @ main.` + 空行）。因此：
   - vendored 文件自身的 Git blob SHA-1 是 `ac1f77139edd80be582b0ef00b9b10d46ef5ec2f`（≠ 协议固定的 `45a22db4...`），SHA-256 是 `2defb2fbacc83e7898a7e9e52d90454bdd1a1fb621ce24d4cea35598f3921176`；
   - 本实验评分一律使用 `experiments/officeqa_structured/official/reward.py`（字节精确副本），不使用 vendored 副本；vendored 副本仅服务于产品侧 `score_stdout` 旧路径。

同名函数清单（两版一致）：`normalize_text`、`_normalize_numeric_formatting`、`extract_numbers_with_context`、`_single_bracketed_list_body`、`_numeric_list_body`、`_parse_numeric_list_item`、`_comma_chunk_spans`、`_segment_numeric_list_values`、`_ground_truth_bracketed_numeric_list`、`_ordered_numbers_match`、`_match_bracketed_numeric_list`、`detect_unit_in_context`、`normalize_number_with_units`、`units_compatible`、`is_likely_year`、`has_significant_text`、`check_text_overlap`、`extract_final_answer_from_xml`、`extract_final_answer`、`fuzzy_match_answer`、`_normalize_direct_text_answer`、`_filter_context_years_for_direct_answer`、`_is_direct_answer_only`、`score_answer`。

## 3. 行为要点（与本实验投影的关系）

- `score_answer(ground_truth, predicted, tolerance=0.0)`：先 `extract_final_answer_from_xml` 取**最后一个** `<FINAL_ANSWER>…</FINAL_ANSWER>` span（`re.IGNORECASE`），再 `_is_direct_answer_only` 形态门（非空、单行、≤250 字符、数值个数与 gold 一致、纯数值 gold 不得带散文、文本 gold 必须归一化后全等），最后 `fuzzy_match_answer` 官方比较。任何异常一律返回 0.0（官方吞异常，不抛出）。
- 因此投影侧必须自己保证：answer 内不允许出现任何大小写形式的 `FINAL_ANSWER`（否则包装后会产生多个 span，取最后一个的语义不再由我们控制）；包装恰好一对标签；`tolerance=0.0` 不可调大救某组。
- "同名单≠同实现"在此被证伪为"确系同实现"，但该结论只对本核对过的 commit 成立；未来上游更新后必须重新跑本文件的对比流程。
