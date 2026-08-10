/* ==========================================================================
   BriefLoop brief_html — local reader and audit pages (production static asset)
   Derived (MIT) from the BriefLoop quality-panel redesign prototype.
   Reads the embedded brief_pages.data.v2 payload and renders:
     tab 1 brief    — exact Store-bound local reader Markdown
     tab 2 quality  — deterministic Store projection (green = pass only)
     tab 3 review   — LAJ semantic advisory view (purple; never PASS wording)
     tab 4 feedback — Store-native Human observation history and guidance state
   Static exports remain read-only. A secured loopback Review Session may expose
   strict Human commands; DOM uses createElement/textContent only.
   ========================================================================== */
(function () {
    "use strict";

    /* ---- i18n ---- */
    var MESSAGES = {
        zh: {
            top_badge: "只读静态导出 · 无任何写入能力",
            session_badge: "本机审阅会话 · 人工操作写入 SQLite",
            tab_brief: "简报",
            tab_quality: "质量状态",
            tab_review: "AI 第二意见",
            tab_feedback: "反馈与改进",
            eyebrow: "审计附件",
            panel_title: "质量面板",
            overall_status: "投影状态",
            meta_run: "运行",
            meta_generated: "生成时间",
            meta_revision: "Store 修订号",
            meta_authority: "权威来源",
            sec_control: "控制面完整性",
            sec_source: "来源与证据",
            sec_gates: "门禁结果",
            sec_claims: "主张支持与风险",
            sec_reader: "读者清洁与引用卫生",
            sec_closeout: "收口与交付包分离",
            sec_actions: "推荐的下一步动作",
            sec_projection: "Store 质量投影原文（JSON）",
            reason_code: "原因码",
            unavailable: "不可用",
            actions_none: "（无推荐动作）",
            laj_title: "AI 第二意见（实验）",
            laj_sub: "以下为冻结仪器对当前终稿的语义层建议，不构成质量分数或交付裁决。",
            laj_not_run: "AI 第二意见尚未运行",
            laj_not_run_note: "本工作区没有可绑定的 AI 第二意见视图；此处不臆造任何评估结果。",
            laj_status: "视图状态",
            reader_review_status_title: "AI 第二意见状态",
            status_finding_returned: "AI 第二意见返回了一个或多个供人工判断的发现项。",
            status_finding_reported: "AI 第二意见报告了一个或多个供人工判断的发现项。",
            status_finding_withheld: "存在发现项，但当前终态暂不披露其内容。",
            status_completed_no_finding: "检查已完成、未返回问题；这不等于通过。",
            status_no_finding_returned_in_completed_supported_checks: "已完成的受支持检查没有返回发现项。",
            status_partially_assessed: "只完成了部分受支持检查；不得据此推断没有发现项。",
            status_unable_to_assess: "AI 第二意见无法完成受支持检查；不得据此推断没有发现项。",
            status_provider_incomplete: "提供方未完成检查；不得据此推断没有发现项。",
            status_provider_refused: "提供方拒绝完成检查；不得据此推断没有发现项。",
            status_provider_retryable_failure: "提供方暂时失败；不得据此推断没有发现项。",
            status_not_assessed: "此终稿尚未运行 AI 第二意见。",
            status_selection_required: "存在多个兼容结果。请选择要展示的结果；系统不会隐式选择最新结果。",
            disclosure_title: "运行前披露与明确授权",
            disclosure_sub: "AI 第二意见只检查 O1 内部一致性与 O2 冻结用户意图/需求覆盖。它是实验性建议，不属于门禁、质量评分或交付决定。",
            disclosure_provider: "兼容协议 / 端点类型",
            disclosure_profile: "固定评估配置",
            disclosure_scope: "发送范围",
            disclosure_budget: "调用次数与 Token 上限",
            disclosure_cost: "费用状态",
            disclosure_retry: "自动重试",
            disclosure_effect: "权威效果",
            disclosure_no_secret: "本页不接收、保存、渲染或记录 API key。凭证只由本机运行环境解析。",
            endpoint_label: "Messages API 地址",
            requested_model_label: "请求的模型 ID",
            model_version_label: "模型版本",
            expected_model_label: "预期模型身份",
            confirm_disclosure: "我已阅读并确认上述固定配置、发送范围、预算、费用未测量、无自动重试及仅供参考/不属于门禁的边界。",
            attest_egress: "我确认终稿与冻结需求可按公共安全报告范围发送至上述提供方。",
            run_reader_review: "授权并运行一次 AI 第二意见",
            refresh_projection: "刷新 Store 核验状态",
            run_fields_required: "请填写全部端点与模型身份字段，并勾选两项确认。",
            command_outcome_unknown: "响应未能确认，外部调用可能已发生。再次操作会复用同一人工请求编号，并先由 Store 解析结果；不会自动重试。",
            pending_external_effect: "AI 第二意见请求已被 Store 接收，但尚无经过 Store 核验的结果；外部调用可能已经发生。系统不会自动重试。请先刷新或检查 Store 状态。",
            selection_title: "选择兼容的 AI 第二意见结果",
            selection_sub: "下列选项均由 Store 针对当前终稿链路和固定 AI 第二意见配置重新核验。内部编号与指纹不作为页面权威，也不显示。",
            selection_choose: "选择此结果",
            result_generation: "评估轮次",
            result_model: "模型",
            result_recorded: "记录时间",
            result_counts: "覆盖情况",
            o2_title: "O2 冻结需求覆盖",
            o2_none: "当前选中结果没有可展示的 O2 冻结需求评估。",
            o2_attention: "需要关注",
            o2_rationale: "评估理由",
            cov_assessed: "已评估单元",
            cov_findings: "发现项",
            cov_withheld: "暂不披露的发现项",
            cov_abstentions: "弃权",
            dim_title: "九个维度概览（按评估单元状态，无分数）",
            dim_finding_reported: "已报告发现项",
            dim_not_assessed: "本视图未评估",
            assessment_summary_title: "本轮为何运行 / 检查了什么",
            assessment_summary_intro: "本轮由人工明确授权触发；触发界面未记录。自动运行已关闭。固定产品检查为 O1 内部一致性与 O2 冻结需求覆盖。",
            assessment_summary_intro_no_evidence: "Store 已提供以下评估范围；授权与触发界面信息未记录。",
            assessment_summary_scope: "检查范围",
            assessment_summary_units: "评估单元",
            assessment_summary_planned: "计划",
            assessment_summary_completed: "已完成",
            assessment_summary_unable: "无法完成",
            assessment_summary_findings: "发现项",
            assessment_summary_withheld: "暂不披露",
            assessment_summary_abstentions: "弃权",
            assessment_summary_trigger: "触发方式",
            assessment_summary_surface: "触发界面",
            assessment_summary_auto: "自动运行",
            assessment_summary_checks: "固定检查",
            assessment_summary_budget: "调用预算",
            assessment_summary_retry: "重试",
            assessment_summary_explicit: "人工明确授权",
            assessment_summary_not_recorded: "未记录",
            assessment_summary_off: "关闭",
            assessment_summary_fixed_checks: "O1 内部一致性 + O2 冻结需求覆盖",
            assessment_summary_two_calls: "2 次调用",
            assessment_summary_no_retry: "无自动重试",
            assessment_summary_technical: "技术详情",
            assessment_summary_technical_note: "内部编号、哈希和调用状态仅在此处显示。",
            assessment_summary_unit_id: "内部单元编号",
            assessment_summary_dimension: "维度",
            assessment_summary_subaspect: "子方面",
            assessment_summary_state: "状态",
            assessment_summary_disposition: "处置",
            assessment_summary_attempt: "调用状态",
            assessment_summary_reason: "原因",
            assessment_summary_hashes: "提示哈希",
            assessment_summary_model: "模型",
            assessment_summary_profile: "固定配置",
            assessment_summary_claimed: "记录时间",
            assessment_summary_call_count: "调用次数",
            f_unit: "评估单元",
            f_observation: "观察",
            f_rationale: "理由",
            f_severity_basis: "严重度依据",
            f_confidence_basis: "置信依据",
            f_action: "建议人工动作",
            f_external_premise: "外部前提披露",
            f_context_reqs: "上下文需求",
            f_rewrite: "建议改写",
            f_spans: "报告定位区间",
            handoff_title: "交接说明（交接项是证据需求，不是缺陷；不会触发门禁）",
            reason_codes_title: "原因代码",
            disclaimer_title: "免责声明",
            fb_title: "反馈与下一轮改进",
            fb_sub: "当前报告或所选评估暂无可展示的 Store 原生人工审阅或已批准指导；本页不会臆造记录。",
            fb_available_sub: "以下为 Store 原生、人工编辑并单独批准的指导；仅在人类明确选择复用并启动后继运行时消费。",
            recorded_title: "已记录的反馈",
            recorded_none: "（暂无记录）",
            il_unavailable: "当前报告或所选评估暂无 Store 原生人工审阅或已批准指导。",
            disposition_title: "人工处置",
            disposition_accept: "接受",
            disposition_reject: "拒绝",
            disposition_defer: "暂缓",
            guidance_edit: "编辑指导",
            guidance_save: "保存草稿",
            guidance_approve: "单独批准",
            guidance_deactivate: "停用",
            guidance_revert: "撤回",
            guidance_supersede: "标记被替代",
            observation_title: "人工独立观察",
            observation_sub: "记录你对终稿的独立观察。它始终绑定冻结终稿；若当前正在查看某个 AI 第二意见结果，也会绑定该精确结果。它不是模型发现项，不会自动进入评估输入或指导。",
            observation_text_label: "观察内容",
            observation_origin: "来源：人工",
            observation_submit: "记录人工观察",
            observation_refs: "可选引用（留空也可以）",
            observation_requirement: "需求编号",
            observation_claim: "主张编号",
            observation_scope: "范围（O1/O2）",
            observation_dimension: "评估维度",
            observation_span: "报告区间（需完整填写）",
            observation_span_report: "报告 SHA-256",
            observation_span_block: "区块编号",
            observation_span_start: "起始字符",
            observation_span_end: "结束字符",
            observation_span_excerpt: "摘录 SHA-256",
            observation_history: "人工观察历史",
            observation_none: "（暂无人工观察）",
            observation_supersede: "替代此观察",
            observation_supersede_text: "新的观察内容",
            observation_supersede_submit: "记录替代版本",
            observation_guidance: "从此观察创建指导草稿",
            observation_binding_report: "仅绑定终稿（未选择 AI 第二意见结果）",
            observation_binding_result: "已绑定所选 AI 第二意见结果",
            observation_invalid_refs: "报告区间必须完整填写，范围（O1/O2）与评估维度必须成对填写。",
            observation_not_allowed: "只有 Store 确认已完成的终稿才能记录人工观察。",
            command_pending: "正在记录…",
            command_failed: "记录失败",
            session_reopen: "审阅会话已过期或已关闭。请重新打开审阅页面，先检查 Store 状态，再决定是否重放；不要盲目重试。",
            session_disconnected: "审阅会话连接已断开。请重新打开审阅页面，先检查 Store 状态，再决定是否重放；不要盲目重试。",
            command_saved: "已由 Store Receipt 记录",
            successor_title: "开始下一轮",
            successor_sub: "从当前冻结终稿启动一个新的 BriefLoop 运行。当前运行方向会由 Store 预填并再次核验。",
            successor_run_id: "下一轮运行编号",
            successor_direction: "冻结的运行方向",
            successor_guidance: "可复用的已批准指导",
            successor_no_guidance: "当前没有可复用的已批准指导。",
            successor_include: "我明确选择将这些指导带入下一轮",
            successor_exclude: "不带入指导（默认）",
            successor_start: "启动下一轮",
            successor_id_required: "请输入安全的下一轮 run ID。",
            successor_direction_changed: "运行方向已变化；请刷新页面后重试。",
            successor_started: "下一轮已由 Core Store 事务启动；页面已刷新。",
            consumption_label: "下一轮消费边界 · ",
            planned_label: "计划中",
            footer_boundary: "静态导出边界：本页永远是只读投影；不含任何命令端点或写入能力。",
            data_error: "嵌入数据缺失或无法解析；无法渲染。",
            tab_aria: "Brief pages sections",
            brief_title: "本地终稿",
            brief_unavailable: "终稿尚不可用",
            brief_local_boundary: "本页仅表示本地完成，不表示审批、打包、交付或发布。",
            brief_progress: "运行进度",
            brief_identity: "Store 工件"
        },
        en: {
            top_badge: "Read-only static export · no write affordance",
            session_badge: "Local Review Session · Human actions write SQLite",
            tab_brief: "Brief",
            tab_quality: "Quality status",
            tab_review: "AI Second Opinion",
            tab_feedback: "Feedback & improvement",
            eyebrow: "Audit attachment",
            panel_title: "Quality Panel",
            overall_status: "Projection status",
            meta_run: "Run",
            meta_generated: "Generated",
            meta_revision: "Store revision",
            meta_authority: "Authority",
            sec_control: "Control integrity",
            sec_source: "Source & evidence",
            sec_gates: "Gate results",
            sec_claims: "Claim support & risk",
            sec_reader: "Reader-clean & citation hygiene",
            sec_closeout: "Closeout & bundle separation",
            sec_actions: "Recommended next actions",
            sec_projection: "Verbatim Store quality projection (JSON)",
            reason_code: "Reason code",
            unavailable: "unavailable",
            actions_none: "(no recommended actions)",
            laj_title: "AI Second Opinion (experimental)",
            laj_sub: "Semantic-layer suggestions from the frozen instrument on the current reader. Not a quality score, not a delivery verdict.",
            laj_not_run: "LAJ not run",
            laj_not_run_note: "No bindable LAJ reader view exists for this workspace; nothing is fabricated here.",
            laj_status: "View status",
            reader_review_status_title: "AI Second Opinion status",
            status_finding_returned: "AI Second Opinion returned one or more advisory findings for Human judgment.",
            status_finding_reported: "AI Second Opinion reported one or more advisory findings for Human judgment.",
            status_finding_withheld: "Findings exist, but their content is withheld for this terminal state.",
            status_completed_no_finding: "The checks completed and returned no finding; this is not a pass.",
            status_no_finding_returned_in_completed_supported_checks: "The completed supported checks returned no finding.",
            status_partially_assessed: "Only part of the supported checks completed. Do not infer that no finding exists.",
            status_unable_to_assess: "AI Second Opinion could not complete the supported checks. Do not infer that no finding exists.",
            status_provider_incomplete: "The provider did not complete the checks. Do not infer that no finding exists.",
            status_provider_refused: "The provider refused to complete the checks. Do not infer that no finding exists.",
            status_provider_retryable_failure: "The provider failed transiently. Do not infer that no finding exists.",
            status_not_assessed: "AI Second Opinion has not been run for this finalized brief.",
            status_selection_required: "Multiple compatible results exist. Choose which result to display; no latest result is selected implicitly.",
            disclosure_title: "Pre-run disclosure and explicit authorization",
            disclosure_sub: "AI Second Opinion checks only O1 internal consistency and O2 frozen user-intent/requirement coverage. It is experimental advice, not a Gate, quality score, or delivery decision.",
            disclosure_provider: "Compatible protocol / endpoint class",
            disclosure_profile: "Fixed profile",
            disclosure_scope: "Sent scope",
            disclosure_budget: "Call and token ceilings",
            disclosure_cost: "Cost status",
            disclosure_retry: "Automatic retry",
            disclosure_effect: "Authority effect",
            disclosure_no_secret: "This page never accepts, stores, renders, or logs an API key. Credentials are resolved only by the local runtime environment.",
            endpoint_label: "Messages endpoint",
            requested_model_label: "Requested model ID",
            model_version_label: "Model version",
            expected_model_label: "Expected model identity",
            confirm_disclosure: "I have read and confirm the profile, sent scope, budget, unmeasured cost, no-auto-retry rule, and advisory/no-Gate boundary above.",
            attest_egress: "I attest that the final brief and frozen requirements may be sent to this provider under the public_safe_report scope.",
            run_reader_review: "Authorize and run one AI Second Opinion",
            refresh_projection: "Refresh Store-qualified status",
            run_fields_required: "Complete every endpoint/model identity field and both confirmations.",
            command_outcome_unknown: "The response could not be confirmed; an external call may have occurred. A repeated click reuses the same Human request ID and resolves Store state first; there is no automatic retry.",
            pending_external_effect: "Store has claimed an AI Second Opinion request, but no Store-qualified result is available. An external call may have occurred. BriefLoop will not retry automatically; refresh or inspect Store state first.",
            selection_title: "Choose a compatible AI Second Opinion result",
            selection_sub: "Every option below was re-qualified by Store against the current final lineage and fixed AI Second Opinion profile. Internal IDs and fingerprints are neither displayed nor treated as page authority.",
            selection_choose: "Use this result",
            result_generation: "Generation",
            result_model: "Model",
            result_recorded: "Recorded",
            result_counts: "Coverage",
            o2_title: "O2 frozen-requirement coverage",
            o2_none: "The selected result has no displayable O2 requirement assessment.",
            o2_attention: "Attention needed",
            o2_rationale: "Assessment rationale",
            cov_assessed: "assessed units",
            cov_findings: "findings",
            cov_withheld: "withheld findings",
            cov_abstentions: "abstentions",
            dim_title: "Nine dimensions by unit status (no scores)",
            dim_finding_reported: "finding reported",
            dim_not_assessed: "not assessed in view",
            assessment_summary_title: "Why this run / what was checked",
            assessment_summary_intro: "This run was triggered by explicit Human authorization; the triggering surface was not recorded. Automatic runs are disabled. Fixed product checks: O1 internal consistency and O2 frozen-requirement coverage.",
            assessment_summary_intro_no_evidence: "Store supplied the assessment inventory below; authorization and triggering-surface evidence was not recorded.",
            assessment_summary_scope: "Scope checked",
            assessment_summary_units: "Assessment units",
            assessment_summary_planned: "planned",
            assessment_summary_completed: "completed",
            assessment_summary_unable: "unable",
            assessment_summary_findings: "findings",
            assessment_summary_withheld: "withheld",
            assessment_summary_abstentions: "abstentions",
            assessment_summary_trigger: "Trigger",
            assessment_summary_surface: "Triggering surface",
            assessment_summary_auto: "Automatic run",
            assessment_summary_checks: "Fixed checks",
            assessment_summary_budget: "Call budget",
            assessment_summary_retry: "Retry",
            assessment_summary_explicit: "explicit Human authorization",
            assessment_summary_not_recorded: "not recorded",
            assessment_summary_off: "off",
            assessment_summary_fixed_checks: "O1 internal consistency + O2 frozen-requirement coverage",
            assessment_summary_two_calls: "2 calls",
            assessment_summary_no_retry: "no automatic retry",
            assessment_summary_technical: "Technical details",
            assessment_summary_technical_note: "Internal IDs, hashes, and call status are shown only here.",
            assessment_summary_unit_id: "Internal unit ID",
            assessment_summary_dimension: "Dimension",
            assessment_summary_subaspect: "Sub-aspect",
            assessment_summary_state: "State",
            assessment_summary_disposition: "Disposition",
            assessment_summary_attempt: "Call status",
            assessment_summary_reason: "Reason",
            assessment_summary_hashes: "Prompt hashes",
            assessment_summary_model: "Model",
            assessment_summary_profile: "Fixed profile",
            assessment_summary_claimed: "Claimed at",
            assessment_summary_call_count: "Call count",
            f_unit: "Assessment unit",
            f_observation: "Observation",
            f_rationale: "Rationale",
            f_severity_basis: "Severity basis",
            f_confidence_basis: "Confidence basis",
            f_action: "Recommended human action",
            f_external_premise: "External premise disclosure",
            f_context_reqs: "Context requirements",
            f_rewrite: "Suggested rewrite",
            f_spans: "Report spans",
            handoff_title: "Handoff note (handoff units are evidence needs, not defects; they never trigger Gates)",
            reason_codes_title: "reason_codes",
            disclaimer_title: "Disclaimer",
            fb_title: "Feedback & next-run improvement",
            fb_sub: "No Store-qualified Human review or approved guidance is available to display for this report or selected assessment; this page fabricates nothing.",
            fb_available_sub: "Store-native Human-edited, separately approved guidance is shown below; it is consumed only by a Human-started successor with explicit reuse opt-in.",
            recorded_title: "Recorded feedback",
            recorded_none: "(no records)",
            il_unavailable: "No Store-qualified Human review or approved guidance is available for this report or selected assessment.",
            disposition_title: "Human disposition",
            disposition_accept: "Accept",
            disposition_reject: "Reject",
            disposition_defer: "Defer",
            guidance_edit: "Edit guidance",
            guidance_save: "Save draft",
            guidance_approve: "Approve separately",
            guidance_deactivate: "Deactivate",
            guidance_revert: "Revert",
            guidance_supersede: "Mark superseded",
            observation_title: "Independent Human observation",
            observation_sub: "Record an independent observation about the finalized brief. It always binds the frozen report; when an AI Second Opinion result is selected, it also binds that exact result. It is not a model finding, does not enter evaluator input, and never becomes guidance automatically.",
            observation_text_label: "Observation",
            observation_origin: "origin=Human",
            observation_submit: "Record Human observation",
            observation_refs: "Optional references (you may leave these blank)",
            observation_requirement: "Requirement ID",
            observation_claim: "Claim ID",
            observation_scope: "O1/O2",
            observation_dimension: "Dimension",
            observation_span: "Report span (fill every field)",
            observation_span_report: "Report SHA-256",
            observation_span_block: "Block ID",
            observation_span_start: "Start character",
            observation_span_end: "End character",
            observation_span_excerpt: "Excerpt SHA-256",
            observation_history: "Human observation history",
            observation_none: "(no Human observations)",
            observation_supersede: "Supersede this observation",
            observation_supersede_text: "New observation text",
            observation_supersede_submit: "Record replacement",
            observation_guidance: "Create guidance draft from this observation",
            observation_binding_report: "report-bound (no AI Second Opinion result selected)",
            observation_binding_result: "Bound to the selected AI Second Opinion result",
            observation_invalid_refs: "A report span must be complete; O1/O2 and dimension must be supplied together.",
            observation_not_allowed: "A Human observation requires a Store-confirmed finalized brief.",
            command_pending: "Recording…",
            command_failed: "Command failed",
            session_reopen: "The review session has expired or closed. Reopen the review page, inspect Store state first, then decide whether to replay; do not blindly retry.",
            session_disconnected: "The review session connection was lost. Reopen the review page, inspect Store state first, then decide whether to replay; do not blindly retry.",
            command_saved: "Recorded by Store Receipt",
            successor_title: "Start the next run",
            successor_sub: "Start a new BriefLoop run from this frozen final brief. Store pre-fills and re-verifies the current RunDirection.",
            successor_run_id: "Successor run ID",
            successor_direction: "Frozen RunDirection",
            successor_guidance: "Approved guidance available for reuse",
            successor_no_guidance: "No approved guidance is available for reuse.",
            successor_include: "I explicitly choose to carry this guidance into the next run",
            successor_exclude: "Do not carry guidance (default)",
            successor_start: "Start successor run",
            successor_id_required: "Enter a safe successor run ID.",
            successor_direction_changed: "RunDirection changed; refresh the page and try again.",
            successor_started: "Core Store transaction started the successor; the page was refreshed.",
            consumption_label: "next-run consumption · ",
            planned_label: "planned",
            footer_boundary: "Static export boundary: this page is always a read-only projection; it contains no command endpoint and no write affordance.",
            data_error: "Embedded data missing or unparseable; cannot render.",
            tab_aria: "Brief pages sections",
            brief_title: "Local final brief",
            brief_unavailable: "Final brief is not available yet",
            brief_local_boundary: "This page records local finalization only. It is not approval, packaging, delivery, or publication.",
            brief_progress: "Run progress",
            brief_identity: "Store artifact"
        }
    };

    /* ---- data ---- */
    var DATA = null;
    try {
        DATA = JSON.parse(document.getElementById("brief-pages-data").textContent);
    } catch (e) {
        DATA = null;
    }

    function readActionSession() {
        try {
            if (location.protocol !== "http:" || location.hostname !== "127.0.0.1") return null;
            var values = new URLSearchParams(location.hash.slice(1));
            var token = values.get("token");
            var session = values.get("session");
            var csrf = values.get("csrf");
            if (!token || !session || !csrf) return null;
            return { token: token, session: session, csrf: csrf };
        } catch (e) {
            return null;
        }
    }

    var ACTION_SESSION = readActionSession();
    var LANG = (
        DATA && DATA.semantic && DATA.semantic.request_template &&
        DATA.semantic.request_template.language === "en"
    ) ? "en" : "zh";
    var STATE = { tab: ACTION_SESSION ? "review" : "brief" };
    var RUN_REQUEST_ID = null;

    function t(key) { return (MESSAGES[LANG] && MESSAGES[LANG][key]) || MESSAGES.zh[key] || key; }

    /* ---- reader-facing labels for protocol values and evaluator enums ---- */
    var ZH_DIMENSION_LABELS = {
        cross_section_consistency: "跨段一致性",
        scope_definition_stability: "范围定义稳定性",
        reasoning_continuity: "推理连贯性",
        uncertainty_calibration: "不确定性校准",
        summary_body_alignment: "摘要与正文一致性",
        recommendation_constraint_consistency: "建议约束一致性",
        brief_requirement_coverage: "简报需求覆盖",
        audience_decision_fit: "受众与决策适配",
        explicit_scope_constraint_compliance: "明确范围约束遵守"
    };
    var ZH_STATUS_LABELS = {
        finding_returned: "已返回发现项",
        finding_reported: "已报告发现项",
        finding_withheld: "发现项暂不披露",
        completed_no_finding: "已完成，未返回问题，不等于通过",
        no_finding_returned_in_completed_supported_checks: "已完成支持检查，未返回发现项",
        partially_assessed: "部分完成评估",
        unable_to_assess: "无法完成评估",
        not_assessed_in_view: "本视图未评估",
        not_assessed: "尚未评估",
        selection_required: "需要选择结果",
        not_run: "尚未运行",
        available: "可用",
        unavailable: "不可用",
        not_available: "不可用",
        stale: "已过期",
        invalid: "无效",
        abstained: "已弃权",
        incomplete: "未完成",
        refused: "已拒绝",
        provider_failed: "提供方失败",
        provider_incomplete: "提供方未完成",
        provider_refused: "提供方拒绝",
        provider_retryable_failure: "提供方暂时失败",
        failed: "失败",
        succeeded: "已成功",
        success: "已成功",
        running: "运行中",
        claimed: "已认领",
        completed: "已完成",
        pending: "待确认",
        recorded: "已记录",
        superseded: "已被替代",
        active: "生效中",
        inactive: "已停用",
        available_guidance: "可用指导",
        unavailable_guidance: "暂无可用指导"
    };
    var ZH_PROTOCOL_LABELS = {
        anthropic_messages_compatible: "Anthropic Messages 兼容协议",
        explicit_messages_api: "明确的 Messages API 端点",
        reader_review: "读者审阅",
        management_monthly: "管理层月报",
        industry_weekly: "行业周报",
        en: "英文",
        zh: "中文",
        zh_CN: "中文",
        "zh-CN": "中文",
        management_brief_en_v1: "管理层简报固定配置",
        industry_weekly_zh_v1: "行业周报固定配置",
        public_safe_report: "公共安全报告范围",
        final_reader_markdown: "终稿读者 Markdown",
        frozen_run_direction_requirements: "冻结运行方向需求",
        O1: "O1 内部一致性",
        O2: "O2 冻结需求覆盖",
        observation_only: "仅限人工观察",
        none: "无",
        not_measured: "未测量",
        key_conclusion: "关键结论",
        decision: "决策",
        scope: "范围",
        recommendation: "建议",
        supporting_text: "支持性文字",
        severe: "严重",
        major: "主要",
        minor: "一般",
        proposal: "待人工判断",
        finding_emitted: "已报告发现项",
        no_finding: "未报告发现项",
        abstain_insufficient_context: "因上下文不足而弃权",
        abstain_unable_to_assess: "因无法评估而弃权",
        abstain_conflicting_context: "因上下文冲突而弃权",
        rubric_not_applicable: "评估标准不适用",
        attention_needed: "需要关注",
        accept: "接受",
        reject: "拒绝",
        defer: "暂缓",
        approve: "批准",
        deactivate: "停用",
        revert: "撤回",
        supersede: "标记被替代",
        fulfilled: "已满足",
        unfulfilled_transparent: "未满足（已透明披露）",
        frozen_requirement: "冻结需求",
        reconcile_status_language: "协调状态表述",
        clarify_scope: "澄清范围",
        repair_reasoning_bridge: "修复推理衔接",
        recalibrate_uncertainty: "重新校准不确定性",
        align_summary_and_body: "对齐摘要与正文",
        review_recommendation_constraints: "复核建议约束",
        address_requirement: "处理需求",
        review_o3_evidence: "复核 O3 外部证据",
        inspect_manually: "人工检查",
        direct_cross_span_conflict: "跨区间直接冲突",
        direct_single_span: "单一区间直接证据",
        explicit_requirement_mismatch: "明确需求不匹配",
        artifact_internal_inference: "工件内部推断",
        ambiguous_scope: "范围含义不明确",
        insufficient_context: "上下文不足",
        key_conclusion_scope: "关键结论",
        public_safe: "公共安全",
        authority_none: "不改变运行权威",
        provider_incomplete: "提供方未完成",
        provider_failed: "提供方失败",
        provider_refused: "提供方拒绝",
        provider_retryable_failure: "提供方暂时失败"
    };
    var ZH_REASON_LABELS = {
        provider_incomplete: "提供方未完成",
        provider_failed: "提供方失败",
        provider_retryable_failure: "提供方可重试失败",
        assessment_completed: "评估已完成",
        reader_review_not_supported: "当前运行不支持 AI 第二意见",
        laj_not_run: "AI 第二意见尚未运行",
        report_binding_stale: "终稿绑定已过期",
        post_final_assessment_pending: "AI 第二意见待确认",
        post_final_assessment_predecessor_outcome_unknown: "前一次 AI 第二意见结果待确认",
        archive_verification_failed: "评估存档核验失败",
        post_final_assessment_binding_invalid: "AI 第二意见绑定无效",
        post_final_assessment_selection_invalid: "AI 第二意见结果选择无效",
        shadow_request_conflict: "评估请求冲突"
    };
    var ZH_NOTE_LABELS = {
        completed_no_finding_not_pass: "完成、未返回问题，不等于通过",
        provider_attempt_incomplete: "提供方未完成",
        finding_reported: "已报告发现项",
        partial_scope: "部分范围完成",
        not_assessed: "尚未评估"
    };

    function localizedEnum(value, labels, includeRaw) {
        var raw = value == null ? "" : String(value);
        if (LANG !== "zh" || !raw) return raw;
        var label = labels[raw];
        return label ? label + (includeRaw ? "（" + raw + "）" : "") : raw;
    }

    function localizedStatus(value) { return localizedEnum(value, ZH_STATUS_LABELS); }
    function localizedProtocol(value) { return localizedEnum(value, ZH_PROTOCOL_LABELS); }
    function localizedReasonCode(value) { return localizedEnum(value, ZH_REASON_LABELS); }
    function localizedNoteCode(value) { return localizedEnum(value, ZH_NOTE_LABELS); }
    function localizedDimension(value) {
        var raw = value == null ? "" : String(value);
        if (LANG !== "zh" || !raw) return raw;
        return ZH_DIMENSION_LABELS[raw] ? ZH_DIMENSION_LABELS[raw] + "（" + raw + "）" : raw;
    }
    function localizedBoolean(value) {
        if (LANG !== "zh") return String(value);
        if (value === true) return "是";
        if (value === false) return "否";
        return String(value);
    }
    function localizedScope(value) { return localizedProtocol(value); }
    function localizedFindingValue(key, value) {
        if (LANG !== "zh") return String(value);
        if (key === "f_confidence_basis" || key === "f_action" ||
                key === "f_external_premise") {
            return localizedProtocol(value);
        }
        return String(value);
    }
    function localizedHandoff(value) {
        var raw = String(value || "");
        if (LANG !== "zh") return raw;
        return raw.replace(
            "Handoff units are evidence needs, not defects; they never trigger Gates.",
            "交接项表示证据需求，不是缺陷；不会触发门禁。"
        );
    }
    function localizedDisclaimer(value) {
        var raw = String(value || "");
        if (LANG !== "zh") return raw;
        var replacements = [
            ["候选 finding 不具有 Gate、Finalize、Delivery、Claim-Support 或发布权威。",
                "候选发现项不具有门禁、终稿、交付、主张支持或发布权威。"],
            ["本次运行未生成可展示的候选 finding。", "本次运行未生成可展示的候选发现项。"],
            ["候选 finding", "候选发现项"],
            ["个 assessment units", "个评估单元"],
            ["个 finding 被保留但未展示", "个发现项暂不披露"],
            ["已评价", "已评估"],
            ["Read-only projection. No Gate, approval, delivery, repair, or runtime authority. LAJ surfaces are Experimental advisory; no finding is neutral and LAJ utility is NOT MEASURED.",
                "只读投影；不具备门禁、审批、交付、修复或运行权威。AI 第二意见仅供实验性参考；没有发现项只是中性结果，效用尚未测量。"],
            ["Exact Store-bound local reader projection; not approval, package, delivery, or publication.",
                "精确绑定 Store 的本地终稿投影；不代表审批、打包、交付或发布。"],
            ["Experimental AI assessment. Advisory only. Not a Gate, delivery decision, or proof of correctness.",
                "实验性 AI 评估，仅供参考；不属于门禁或交付决定，也不能证明内容正确。"],
            ["Utility NOT MEASURED.", "效用尚未测量。"],
            ["Utility is NOT MEASURED.", "效用尚未测量。"],
            ["Experimental advisory assessment.", "实验性 AI 评估。"],
            ["This assessment is advisory only.", "本评估仅供参考。"],
            ["Candidate findings have no Gate, finalization, delivery, repair, approval, or next-action authority.",
                "候选发现项不具备门禁、终稿、交付、修复、审批或下一步动作权威。"],
            ["No finding was returned in the completed supported checks.",
                "已完成的受支持检查没有返回可展示的发现项。"],
            ["This is not a quality pass and does not verify facts, source quality, strategic correctness, or publication readiness.",
                "这不是质量通过，也不核实事实、来源质量、战略正确性或发布准备度。"],
            ["This is not a quality pass.", "这不是质量通过。"],
            ["No finding was returned.", "未返回发现项。"],
            ["This does not mean the report is correct, complete, or ready for delivery.",
                "这不表示报告正确、完整或已可交付。"],
            ["Experimental advisory assessment is unavailable, invalid, stale, or abstained.",
                "实验性 AI 评估不可用、无效、过期或已弃权。"],
            ["Experimental advisory assessment is not available.", "实验性 AI 评估不可用。"],
            ["Experimental advisory terminal status is recorded without actionable findings.",
                "已记录实验性 AI 评估的终态，但没有可执行的发现项。"],
            ["The current archive was not semantically reverified.", "当前存档未重新进行语义核验。"],
            ["No workflow, Gate, finalization, delivery, repair, approval, or next-action effect.",
                "不会影响工作流、门禁、终稿、交付、修复、审批或下一步动作。"],
            ["No workflow, Gate, finalization, delivery, repair, approval, or next-action authority.",
                "不具备工作流、门禁、终稿、交付、修复、审批或下一步动作权威。"],
            ["Status:", "状态："],
            ["matched_non_LLM", "非模型匹配基线"],
            ["not_applicable", "不适用"],
            ["provider_incomplete", "提供方未完成"],
            ["provider_failed", "提供方失败"],
            ["provider_refused", "提供方拒绝"],
            ["assessed", "已评估"],
            ["units", "个单元"],
            ["abstentions", "个弃权单元"],
            ["terminal failures", "个终态失败"],
            ["withheld findings", "个暂不披露的发现项"]
        ];
        replacements.forEach(function (pair) { raw = raw.split(pair[0]).join(pair[1]); });
        return raw;
    }

    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text != null) n.textContent = text;
        return n;
    }

    /* ---- value rendering: strings/numbers as text; arrays/objects as compact JSON ---- */
    function valueNode(v) {
        if (v === null || v === undefined) return el("span", "kv-null", "null");
        if (typeof v === "object") return el("code", null, JSON.stringify(v));
        return el("span", null, String(v));
    }

    function requestId(prefix) {
        if (!window.crypto || !window.crypto.getRandomValues) return null;
        var values = new Uint32Array(4);
        window.crypto.getRandomValues(values);
        return prefix + "-" + Array.from(values).map(function (value) {
            return value.toString(16).padStart(8, "0");
        }).join("");
    }

    function isOutcomeUnknownReason(reason) {
        return reason === "post_final_assessment_pending" ||
            reason === "post_final_assessment_predecessor_outcome_unknown";
    }

    function isClosedSessionReason(reason) {
        return reason === "review_session_expired" ||
            reason === "review_session_replaced";
    }

    function commandFailureMessage(reason, action) {
        if (isClosedSessionReason(reason)) return t("session_reopen");
        if (action === "run_reader_review") return t("command_outcome_unknown");
        return t("command_failed");
    }

    function sendReviewCommand(action, payload, statusNode, button, onSuccess) {
        if (!ACTION_SESSION) return;
        if (button) button.disabled = true;
        statusNode.textContent = t("command_pending");
        fetch("/api/v1/command?session_id=" + encodeURIComponent(ACTION_SESSION.session), {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-BriefLoop-Session-Token": ACTION_SESSION.token,
                "X-BriefLoop-CSRF-Token": ACTION_SESSION.csrf
            },
            body: JSON.stringify({
                schema_version: "briefloop.post_final_review.command.v1",
                action: action,
                payload: payload
            })
        }).then(function (response) {
            return response.text().then(function (body) {
                var result = null;
                try { result = body ? JSON.parse(body) : null; } catch (e) { /* value-free */ }
                if (!response.ok) {
                    var rejected = new Error("command rejected");
                    rejected.reasonCode = result && result.reason_code;
                    throw rejected;
                }
                if (!result || typeof result !== "object") {
                    throw new Error("command response invalid");
                }
                return result;
            });
        }).then(function (result) {
            if (result.page_data && typeof result.page_data === "object" &&
                    result.page_data.schema_version === "briefloop.brief_pages.data.v2") {
                DATA = result.page_data;
            }
            if (result.ok !== true) {
                statusNode.textContent = isOutcomeUnknownReason(result.reason_code) ?
                    t("pending_external_effect") :
                    localizedReasonCode(result.reason_code || t("command_failed"));
                if (button) button.disabled = false;
                return;
            }
            if (onSuccess) onSuccess(result);
            if (result.page_data) {
                renderAll();
            } else if (action === "start_successor") {
                statusNode.textContent = t("successor_started");
                if (button) button.disabled = true;
            } else {
                statusNode.textContent = t("command_saved");
                if (button) button.disabled = false;
            }
        }).catch(function (error) {
            statusNode.textContent = error && error.reasonCode ?
                commandFailureMessage(error.reasonCode, action) :
                (action === "run_reader_review" ?
                    t("command_outcome_unknown") : t("session_disconnected"));
            if (button) button.disabled = false;
        });
    }

    /* ---- brief tab: deliberately small Markdown subset, DOM/textContent only ---- */
    function markdownBlocks(markdown) {
        var fragment = document.createDocumentFragment();
        var lines = String(markdown || "").replace(/\r\n?/g, "\n").split("\n");
        var list = null;
        var code = null;

        function flushList() {
            if (list) {
                fragment.appendChild(list);
                list = null;
            }
        }
        lines.forEach(function (line) {
            if (line.slice(0, 3) === "```") {
                flushList();
                if (code) {
                    fragment.appendChild(code);
                    code = null;
                } else {
                    code = el("pre", "reader-code");
                }
                return;
            }
            if (code) {
                code.appendChild(document.createTextNode(
                    (code.textContent ? "\n" : "") + line
                ));
                return;
            }
            var heading = /^(#{1,3})\s+(.+)$/.exec(line);
            if (heading) {
                flushList();
                fragment.appendChild(el("h" + heading[1].length, null, heading[2]));
                return;
            }
            var bullet = /^\s*[-*]\s+(.+)$/.exec(line);
            var ordered = /^\s*\d+\.\s+(.+)$/.exec(line);
            if (bullet || ordered) {
                var kind = ordered ? "ol" : "ul";
                if (!list || list.tagName.toLowerCase() !== kind) {
                    flushList();
                    list = el(kind);
                }
                list.appendChild(el("li", null, (bullet || ordered)[1]));
                return;
            }
            flushList();
            if (/^\s*$/.test(line)) return;
            if (/^>\s?/.test(line)) {
                fragment.appendChild(el("blockquote", null, line.replace(/^>\s?/, "")));
            } else {
                fragment.appendChild(el("p", null, line));
            }
        });
        flushList();
        if (code) fragment.appendChild(code);
        return fragment;
    }

    function renderBrief(main) {
        var brief = DATA.brief || {};
        var run = DATA.run || {};
        var section = el("article", "reader-sheet");
        var header = el("header", "reader-header");
        header.appendChild(el("p", "eyebrow", t("brief_title")));
        header.appendChild(el("h1", null,
            brief.status === "available" ? t("brief_title") : t("brief_unavailable")));
        header.appendChild(el("p", "hero-boundary",
            localizedDisclaimer(brief.boundary || t("brief_local_boundary"))));
        var state = el("span", "status-pill-mini " +
            (brief.status === "available" ? "level-pass" : "level-missing"),
            localizedStatus(brief.view_state || run.view_state || "setup"));
        header.appendChild(state);
        section.appendChild(header);

        if (brief.status === "available" && typeof brief.markdown === "string") {
            var identity = brief.artifact || {};
            var meta = el("p", "reader-identity");
            meta.appendChild(el("strong", null, t("brief_identity") + " · "));
            meta.appendChild(el("code", null,
                String(identity.artifact_id || "") + "@" +
                String(identity.revision || "") + " · sha256 " +
                String(identity.sha256 || "")));
            section.appendChild(meta);
            var body = el("div", "reader-markdown");
            body.appendChild(markdownBlocks(brief.markdown));
            section.appendChild(body);
            section.appendChild(el("p", "reader-terminal-note", t("brief_local_boundary")));
        } else {
            var progress = el("div", "unavailable-card");
            progress.appendChild(el("strong", null, t("brief_progress")));
            progress.appendChild(el("p", null,
                String(run.completed_stages || 0) + "/" +
                String(run.total_stages || 0) + " · " +
                String(run.reason_code || brief.reason_code || "")));
            section.appendChild(progress);
        }
        main.appendChild(section);
    }

    /* ---- quality tab ---- */
    function renderHero(main) {
        var q = DATA.quality || {};
        var hero = el("header", "panel-hero");
        var left = el("div");
        left.appendChild(el("p", "eyebrow", t("eyebrow")));
        left.appendChild(el("h1", null, t("panel_title")));
        left.appendChild(el("p", "hero-boundary", localizedDisclaimer(DATA.boundary || "")));
        hero.appendChild(left);

        var available = q.status === "available";
        var pill = el("div", "status-pill " + (available ? "level-pass" : "level-missing"));
        pill.appendChild(el("span", "sp-k", t("overall_status")));
        pill.appendChild(el("span", "sp-v", localizedStatus(q.status || t("unavailable"))));
        if (!available && q.reason_code) {
            pill.appendChild(el("span", "sp-k", t("reason_code") + ": " + localizedReasonCode(q.reason_code)));
        }
        hero.appendChild(pill);

        var ws = DATA.workspace || {};
        var meta = el("div", "hero-meta");
        [["meta_run", ws.run_id], ["meta_generated", DATA.generated_at],
         ["meta_revision", ws.store_revision], ["meta_authority", ws.authority]].forEach(function (kv) {
            var span = el("span");
            span.appendChild(el("span", "k", t(kv[0])));
            span.appendChild(valueNode(kv[1]));
            meta.appendChild(span);
        });
        hero.appendChild(meta);
        main.appendChild(hero);
    }

    function renderGroup(main, titleKey, rows) {
        var sec = el("section", "panel-section");
        sec.appendChild(el("h2", null, t(titleKey)));
        var tb = el("table", "kv-table");
        (rows || []).forEach(function (r) {
            var tr = el("tr");
            tr.appendChild(el("th", null, typeof r.label === "string" ? r.label : JSON.stringify(r.label)));
            var td = el("td");
            var tone = r.tone || "neutral";
            var node = valueNode(r.value);
            if (tone !== "neutral") {
                var wrap = el("span", "kv-tone-" + tone);
                wrap.appendChild(node);
                td.appendChild(wrap);
            } else {
                td.appendChild(node);
            }
            tr.appendChild(td);
            tb.appendChild(tr);
        });
        sec.appendChild(tb);
        main.appendChild(sec);
    }

    function renderActions(main) {
        var actions = (DATA.quality && DATA.quality.actions) || [];
        var sec = el("section", "panel-section");
        sec.appendChild(el("h2", null, t("sec_actions")));
        if (!actions.length) {
            sec.appendChild(el("p", "section-muted", t("actions_none")));
        } else {
            var ul = el("ul", "actions-list");
            actions.forEach(function (a) {
                var li = el("li");
                li.appendChild(el("strong", null, String(a.action_kind || "action")));
                var detail = el("span", null,
                    String(a.reason_code || "") +
                    (a.effect_kind ? " · " + a.effect_kind : "") +
                    (a.stage_id ? " · " + a.stage_id : "") +
                    (a.role_id ? " · " + a.role_id : ""));
                li.appendChild(detail);
                li.appendChild(el("code", "action-json", JSON.stringify(a)));
                ul.appendChild(li);
            });
            sec.appendChild(ul);
        }
        main.appendChild(sec);
    }

    function renderProjection(main) {
        var det = el("details", "projection-details");
        det.appendChild(el("summary", null, t("sec_projection")));
        det.appendChild(el("pre", null, JSON.stringify((DATA.quality || {}).projection, null, 2)));
        main.appendChild(det);
    }

    function renderQuality(main) {
        renderHero(main);
        var groups = (DATA.quality || {}).groups || {};
        renderGroup(main, "sec_control", groups.control);
        renderGroup(main, "sec_source", groups.source);
        renderGroup(main, "sec_gates", groups.gates);
        renderGroup(main, "sec_claims", groups.claims);
        renderGroup(main, "sec_reader", groups.reader_clean);
        renderGroup(main, "sec_closeout", groups.closeout);
        renderActions(main);
        renderProjection(main);
    }

    /* ---- review tab (LAJ advisory; purple; no PASS wording anywhere) ---- */
    function renderIdentityCompact(main) {
        var q = DATA.quality || {};
        var ws = DATA.workspace || {};
        var strip = el("div", "identity-strip");
        var available = q.status === "available";
        strip.appendChild(el("span", "status-pill-mini " + (available ? "level-pass" : "level-missing"), localizedStatus(q.status || t("unavailable"))));
        strip.appendChild(el("span", "identity-meta",
            String(ws.run_id || "") + " · rev " + String(ws.store_revision || "")));
        main.appendChild(strip);
    }

    function disclosureFact(labelKey, value) {
        var row = el("div", "disclosure-fact");
        row.appendChild(el("span", "fb-k", t(labelKey)));
        row.appendChild(el("span", null, String(value)));
        return row;
    }

    function labeledTextInput(parent, labelKey, maxLength) {
        var label = el("label", "reader-review-field");
        label.appendChild(el("span", "fb-k", t(labelKey)));
        var input = document.createElement("input");
        input.type = "text";
        input.maxLength = maxLength;
        input.autocomplete = "off";
        input.spellcheck = false;
        label.appendChild(input);
        parent.appendChild(label);
        return input;
    }

    function labeledConfirmation(parent, labelKey) {
        var label = el("label", "reader-review-confirmation");
        var input = document.createElement("input");
        input.type = "checkbox";
        label.appendChild(input);
        label.appendChild(el("span", null, t(labelKey)));
        parent.appendChild(label);
        return input;
    }

    function renderResultSelection(parent, sem) {
        if (!(ACTION_SESSION && sem.selection_required === true)) return;
        var section = el("section", "result-selection-card");
        section.appendChild(el("h3", null, t("selection_title")));
        section.appendChild(el("p", "section-muted", t("selection_sub")));
        var commandStatus = el("p", "reader-review-command-status");
        (sem.compatible_result_options || []).forEach(function (option) {
            var row = el("div", "result-option");
            var details = el("div", "result-option-details");
            details.appendChild(el("strong", null,
                t("result_generation") + " " + String(option.assessment_generation)));
            details.appendChild(el("span", null,
                t("result_model") + " · " + String(option.requested_model_id || "") +
                " / " + String(option.model_version || "")));
            details.appendChild(el("span", null,
                t("result_recorded") + " · " + String(option.recorded_at || "")));
            details.appendChild(el("span", null,
                t("result_counts") + " · " +
                (LANG === "zh"
                    ? String(option.assessed_unit_count || 0) + " 个已评估单元 · " +
                      String(option.finding_count || 0) + " 个发现项 · " +
                      String(option.withheld_finding_count || 0) + " 个暂不披露的发现项 · " +
                      String(option.abstention_count || 0) + " 个弃权单元 · " +
                      localizedStatus(option.terminal_evidence_class || "")
                    : String(option.assessed_unit_count || 0) + " assessed · " +
                      String(option.finding_count || 0) + " findings · " +
                      String(option.withheld_finding_count || 0) + " withheld · " +
                      String(option.abstention_count || 0) + " abstentions · " +
                      String(option.terminal_evidence_class || ""))));
            row.appendChild(details);
            var choose = el("button", "reader-review-action secondary", t("selection_choose"));
            choose.type = "button";
            choose.addEventListener("click", function () {
                sendReviewCommand("select_result", {
                    schema_version: "briefloop.post_final_review.reader_review_selection.v1",
                    assessment_result_id: option.assessment_result_id,
                    assessment_result_fingerprint: option.assessment_result_fingerprint
                }, commandStatus, choose);
            });
            row.appendChild(choose);
            section.appendChild(row);
        });
        section.appendChild(commandStatus);
        parent.appendChild(section);
    }

    function renderReaderReviewControl(parent, sem) {
        var status = String(sem.status || "not_assessed");
        var statusCard = el("section", "reader-review-status");
        statusCard.appendChild(el("span", "fb-k", t("reader_review_status_title")));
        statusCard.appendChild(el("strong", null, localizedStatus(status)));
        statusCard.appendChild(el("p", null, t("status_" + status)));
        if ((sem.reason_codes || []).some(isOutcomeUnknownReason)) {
            statusCard.appendChild(el("p", "outcome-unknown-note",
                t("pending_external_effect")));
        }
        if (ACTION_SESSION) {
            var refreshStatus = el("span", "reader-review-command-status");
            var refresh = el("button", "reader-review-action secondary", t("refresh_projection"));
            refresh.type = "button";
            refresh.addEventListener("click", function () {
                sendReviewCommand("refresh", {
                    schema_version: "briefloop.post_final_review.refresh.v1"
                }, refreshStatus, refresh);
            });
            statusCard.appendChild(refresh);
            statusCard.appendChild(refreshStatus);
        }
        parent.appendChild(statusCard);

        renderResultSelection(parent, sem);

        var template = sem.request_template;
        if (!template) return;
        var disclosure = el("section", "reader-review-disclosure");
        disclosure.appendChild(el("h3", null, t("disclosure_title")));
        disclosure.appendChild(el("p", "advisory-sub", t("disclosure_sub")));
        var facts = el("div", "disclosure-grid");
        facts.appendChild(disclosureFact("disclosure_provider",
            localizedProtocol(template.protocol) + " · " + localizedProtocol(template.endpoint_class)));
        facts.appendChild(disclosureFact("disclosure_profile",
            localizedProtocol(template.assessment_kind) + " · " + localizedProtocol(template.report_type) +
            " · " + localizedProtocol(template.language) + " · " + localizedProtocol(template.profile_id)));
        facts.appendChild(disclosureFact("disclosure_scope",
            localizedScope(template.egress_scope) + " · " + localizedScope(template.report_scope) +
            " · " + localizedScope(template.context_scope)));
        facts.appendChild(disclosureFact("disclosure_budget",
            LANG === "zh"
                ? String(template.provider_call_ceiling) + " 次调用 · 输入最多 " +
                  String(template.total_input_token_ceiling) + " Token · 输出总量最多 " +
                  String(template.total_output_token_ceiling) + " Token · 单次输出最多 " +
                  String(template.output_tokens_per_call) + " Token"
                : String(template.provider_call_ceiling) + " calls · " +
                  String(template.total_input_token_ceiling) + " input · " +
                  String(template.total_output_token_ceiling) + " output total · " +
                  String(template.output_tokens_per_call) + " output/call"));
        facts.appendChild(disclosureFact("disclosure_cost", localizedProtocol(template.cost_status)));
        facts.appendChild(disclosureFact("disclosure_retry",
            LANG === "zh"
                ? (template.automatic_retry === false ? "无自动重试" : localizedBoolean(template.automatic_retry))
                : (template.automatic_retry === false ? "none" : String(template.automatic_retry))));
        facts.appendChild(disclosureFact("disclosure_effect",
            LANG === "zh"
                ? "仅供参考 · 不改变运行权威 · 不属于门禁"
                : "advisory_only=" + String(template.advisory_only) +
                  " · authority_effect=" + String(template.authority_effect) + " · not a Gate"));
        disclosure.appendChild(facts);
        disclosure.appendChild(el("p", "credential-boundary", t("disclosure_no_secret")));

        if (ACTION_SESSION && sem.run_action_available === true &&
                status === "not_assessed") {
            var fields = el("div", "reader-review-fields");
            var endpoint = labeledTextInput(fields, "endpoint_label", 2048);
            var requestedModel = labeledTextInput(fields, "requested_model_label", 512);
            var modelVersion = labeledTextInput(fields, "model_version_label", 512);
            var expectedModel = labeledTextInput(fields, "expected_model_label", 512);
            disclosure.appendChild(fields);
            var confirmed = labeledConfirmation(disclosure, "confirm_disclosure");
            var egress = labeledConfirmation(disclosure, "attest_egress");
            var commandStatus = el("p", "reader-review-command-status");
            var run = el("button", "reader-review-action primary", t("run_reader_review"));
            run.type = "button";
            run.addEventListener("click", function () {
                var values = [endpoint.value, requestedModel.value,
                    modelVersion.value, expectedModel.value].map(function (value) {
                    return String(value || "").trim();
                });
                if (values.some(function (value) { return !value; }) ||
                        !confirmed.checked || !egress.checked) {
                    commandStatus.textContent = t("run_fields_required");
                    return;
                }
                if (!RUN_REQUEST_ID) RUN_REQUEST_ID = requestId("reader-review");
                if (!RUN_REQUEST_ID) {
                    commandStatus.textContent = t("command_failed");
                    return;
                }
                sendReviewCommand("run_reader_review", {
                    schema_version: "briefloop.reader_review_assessment_input.v1",
                    human_actor_id: "local-human-reviewer",
                    human_request_id: RUN_REQUEST_ID,
                    disclosure_confirmed: confirmed.checked,
                    messages_endpoint: values[0],
                    requested_model_id: values[1],
                    model_version: values[2],
                    expected_model_identity: values[3],
                    public_safe_egress_attested: egress.checked,
                    cost_status: "not_measured"
                }, commandStatus, run);
            });
            disclosure.appendChild(run);
            disclosure.appendChild(commandStatus);
        }
        parent.appendChild(disclosure);
    }

    function assessmentCount(value) {
        return String(value == null ? 0 : value);
    }

    function assessmentInventorySummary(scopeCount, unitCount) {
        if (LANG === "zh") {
            return "本轮覆盖 " + assessmentCount(scopeCount) + " 个范围、" +
                assessmentCount(unitCount) + " 个评估单元。";
        }
        return "This run covers " + assessmentCount(scopeCount) + " scope(s) and " +
            assessmentCount(unitCount) + " assessment unit(s).";
    }

    function assessmentScopeCounts(scope) {
        var counts = [
            t("assessment_summary_planned") + " " + assessmentCount(scope.planned_unit_count),
            t("assessment_summary_completed") + " " + assessmentCount(scope.completed_unit_count),
            t("assessment_summary_unable") + " " + assessmentCount(scope.unable_unit_count),
            t("assessment_summary_findings") + " " + assessmentCount(scope.finding_unit_count),
            t("assessment_summary_withheld") + " " + assessmentCount(scope.withheld_unit_count),
            t("assessment_summary_abstentions") + " " + assessmentCount(scope.abstention_unit_count)
        ];
        return counts.join(" · ");
    }

    function appendAssessmentTechnicalRow(parent, label, value, raw) {
        var row = el("div", "disclosure-fact");
        row.appendChild(el("span", "fb-k", label));
        if (raw) {
            row.appendChild(el("code", null, String(value == null ? "" : value)));
        } else {
            row.appendChild(el("span", null, String(value == null ? "" : value)));
        }
        parent.appendChild(row);
    }

    function renderAssessmentPlanSummary(parent, sem) {
        var scopes = Array.isArray(sem.assessment_scopes) ? sem.assessment_scopes : [];
        var units = Array.isArray(sem.assessment_units) ? sem.assessment_units : [];
        var evidence = sem.run_evidence || null;
        if (!scopes.length && !units.length && !evidence) return false;

        var section = el("section", "reader-review-disclosure assessment-plan-summary");
        section.appendChild(el("h3", null, t("assessment_summary_title")));
        section.appendChild(el("p", "advisory-sub", evidence ?
            t("assessment_summary_intro") : t("assessment_summary_intro_no_evidence")));
        section.appendChild(el("p", "section-muted",
            assessmentInventorySummary(scopes.length, units.length)));

        if (scopes.length) {
            section.appendChild(el("h4", null, t("assessment_summary_scope")));
            var scopeList = el("div", "requirement-assessment-list");
            scopes.forEach(function (scope) {
                var card = el("article", "requirement-assessment");
                var head = el("div", "finding-head");
                head.appendChild(el("strong", null, localizedScope(scope.scope_class || "")));
                head.appendChild(el("span", "badge badge-advisory",
                    localizedStatus(scope.state || "not_assessed")));
                card.appendChild(head);
                card.appendChild(el("p", "requirement-rationale",
                    assessmentScopeCounts(scope)));
                if (scope.note_code) {
                    var note = el("p", "requirement-rationale");
                    note.appendChild(el("span", "fb-k", t("assessment_summary_reason") + " · "));
                    note.appendChild(el("span", null, localizedNoteCode(scope.note_code)));
                    card.appendChild(note);
                }
                scopeList.appendChild(card);
            });
            section.appendChild(scopeList);
        }

        if (evidence) {
            var evidenceGrid = el("div", "disclosure-grid");
            appendAssessmentTechnicalRow(evidenceGrid, t("assessment_summary_trigger"),
                LANG === "zh" ? t("assessment_summary_explicit") :
                    String(evidence.trigger || ""));
            appendAssessmentTechnicalRow(evidenceGrid, t("assessment_summary_surface"),
                LANG === "zh" ? t("assessment_summary_not_recorded") :
                    String(evidence.surface || ""));
            appendAssessmentTechnicalRow(evidenceGrid, t("assessment_summary_auto"),
                LANG === "zh" ? t("assessment_summary_off") :
                    String(evidence.auto_run === false ? "off" : evidence.auto_run));
            appendAssessmentTechnicalRow(evidenceGrid, t("assessment_summary_checks"),
                t("assessment_summary_fixed_checks"));
            var calls = evidence.provider_call_count == null ?
                (Array.isArray(evidence.calls) ? evidence.calls.length : 0) :
                evidence.provider_call_count;
            appendAssessmentTechnicalRow(evidenceGrid, t("assessment_summary_budget"),
                calls === 2 ? t("assessment_summary_two_calls") :
                    (LANG === "zh" ? assessmentCount(calls) + " 次调用" :
                        assessmentCount(calls) + " calls"));
            appendAssessmentTechnicalRow(evidenceGrid, t("assessment_summary_retry"),
                evidence.automatic_retry === false ? t("assessment_summary_no_retry") :
                    (LANG === "zh" ? localizedBoolean(evidence.automatic_retry) :
                        String(evidence.automatic_retry)));
            section.appendChild(evidenceGrid);
        }

        var details = el("details", "projection-details");
        details.appendChild(el("summary", null, t("assessment_summary_technical")));
        details.appendChild(el("p", "section-muted", t("assessment_summary_technical_note")));

        if (evidence) {
            var evidenceDetails = el("div", "disclosure-grid");
            appendAssessmentTechnicalRow(evidenceDetails, t("assessment_summary_model"),
                String(evidence.requested_model_id || "") + " · " +
                String(evidence.model_version || "") + " · " +
                String(evidence.expected_model_identity || ""), true);
            appendAssessmentTechnicalRow(evidenceDetails, t("assessment_summary_profile"),
                String(evidence.profile_id || ""), true);
            appendAssessmentTechnicalRow(evidenceDetails, t("assessment_summary_claimed"),
                String(evidence.claimed_at || ""));
            appendAssessmentTechnicalRow(evidenceDetails, "human_actor_id",
                String(evidence.human_actor_id || ""), true);
            appendAssessmentTechnicalRow(evidenceDetails, "human_request_id",
                String(evidence.human_request_id || ""), true);
            appendAssessmentTechnicalRow(evidenceDetails, "assessment_request_id",
                String(evidence.assessment_request_id || ""), true);
            appendAssessmentTechnicalRow(evidenceDetails, "assessment_request_fingerprint",
                String(evidence.assessment_request_fingerprint || ""), true);
            appendAssessmentTechnicalRow(evidenceDetails, "policy_revision_id",
                String(evidence.policy_revision_id || ""), true);
            appendAssessmentTechnicalRow(evidenceDetails, "policy_fingerprint",
                String(evidence.policy_fingerprint || ""), true);
            appendAssessmentTechnicalRow(evidenceDetails, "provider_call_count",
                String(evidence.provider_call_count == null ? "" : evidence.provider_call_count), true);
            var promptHashes = evidence.ordered_prompt_request_sha256s || [];
            if (!Array.isArray(promptHashes)) promptHashes = [promptHashes];
            appendAssessmentTechnicalRow(evidenceDetails, "ordered_prompt_request_sha256s",
                promptHashes.join(" · "), true);
            appendAssessmentTechnicalRow(evidenceDetails, "system_prompt_sha256",
                String(evidence.system_prompt_sha256 || ""), true);
            appendAssessmentTechnicalRow(evidenceDetails, "dimension_prompt_sha256",
                String(evidence.dimension_prompt_sha256 || ""), true);
            appendAssessmentTechnicalRow(evidenceDetails, "assessment_plan_sha256",
                String(evidence.assessment_plan_sha256 || ""), true);
            appendAssessmentTechnicalRow(evidenceDetails, "input_binding_sha256",
                String(evidence.input_binding_sha256 || ""), true);
            appendAssessmentTechnicalRow(evidenceDetails, "instrument_sha256",
                String(evidence.instrument_sha256 || ""), true);
            details.appendChild(evidenceDetails);

            var callsList = Array.isArray(evidence.calls) ? evidence.calls : [];
            if (callsList.length) {
                details.appendChild(el("h4", null, t("assessment_summary_call_count")));
                var callsBox = el("div", "requirement-assessment-list");
                callsList.forEach(function (call) {
                    var callCard = el("article", "requirement-assessment");
                    var callHead = el("div", "finding-head");
                    callHead.appendChild(el("strong", null,
                        String(call.dimension_id || "")));
                    callHead.appendChild(el("span", "badge badge-advisory",
                        localizedStatus(call.status || "")));
                    callCard.appendChild(callHead);
                    appendAssessmentTechnicalRow(callCard, t("assessment_summary_reason"),
                        String(call.reason_code || ""), true);
                    appendAssessmentTechnicalRow(callCard, t("assessment_summary_hashes"),
                        String(call.prompt_request_sha256 || ""), true);
                    callsBox.appendChild(callCard);
                });
                details.appendChild(callsBox);
            }
        }

        if (units.length) {
            details.appendChild(el("h4", null, t("assessment_summary_units")));
            var unitsList = el("div", "requirement-assessment-list");
            units.forEach(function (unit) {
                var card = el("article", "requirement-assessment");
                var head = el("div", "finding-head");
                head.appendChild(el("strong", null, String(unit.assessment_unit_id || "")));
                head.appendChild(el("span", "badge badge-advisory",
                    localizedStatus(unit.state || "not_assessed")));
                card.appendChild(head);
                var meta = el("p", "requirement-rationale");
                meta.appendChild(el("span", "fb-k", t("assessment_summary_scope") + " · "));
                meta.appendChild(el("span", null, localizedScope(unit.scope_class || "")));
                meta.appendChild(el("span", null, " · " + t("assessment_summary_dimension") + " · "));
                meta.appendChild(el("code", null, String(unit.dimension_id || "")));
                meta.appendChild(el("span", null, " · " + t("assessment_summary_subaspect") + " · "));
                meta.appendChild(el("code", null, String(unit.sub_aspect_id || "")));
                card.appendChild(meta);
                var status = el("p", "requirement-rationale");
                status.appendChild(el("span", "fb-k", t("assessment_summary_disposition") + " · "));
                status.appendChild(el("span", null, localizedProtocol(unit.disposition || "")));
                status.appendChild(el("span", null, " · " + t("assessment_summary_attempt") + " · "));
                status.appendChild(el("span", null, localizedStatus(unit.attempt_status || "")));
                if (unit.reason_code) {
                    status.appendChild(el("span", null, " · " + t("assessment_summary_reason") + " · "));
                    status.appendChild(el("code", null, String(unit.reason_code)));
                }
                card.appendChild(status);
                unitsList.appendChild(card);
            });
            details.appendChild(unitsList);
        }
        section.appendChild(details);
        parent.appendChild(section);
        return scopes.length > 0 || units.length > 0;
    }

    function renderRequirementAssessments(parent, sem) {
        var assessments = sem.requirement_assessments || [];
        if (!assessments.length) {
            if (LANG === "zh") {
                parent.appendChild(el("h2", null, t("o2_title")));
                parent.appendChild(el("p", "section-muted", t("o2_none")));
            }
            return;
        }
        parent.appendChild(el("h2", null, t("o2_title")));
        var list = el("div", "requirement-assessment-list");
        assessments.forEach(function (assessment) {
            var attention = assessment.attention_status !== "none";
            var card = el("article", "requirement-assessment" +
                (attention ? " attention" : ""));
            var head = el("div", "finding-head");
            head.appendChild(el("strong", null,
                localizedProtocol(assessment.requirement_type || "frozen_requirement")));
            head.appendChild(el("span", "badge badge-advisory", localizedStatus(assessment.state || "")));
            if (attention) {
                head.appendChild(el("span", "badge badge-missing",
                    t("o2_attention") + " · " + localizedProtocol(assessment.attention_status)));
            }
            card.appendChild(head);
            card.appendChild(el("p", "requirement-text",
                String(assessment.requirement_text || "")));
            var rationale = el("p", "requirement-rationale");
            rationale.appendChild(el("span", "fb-k", t("o2_rationale") + " · "));
            rationale.appendChild(el("span", null, String(assessment.rationale || "")));
            card.appendChild(rationale);
            card.appendChild(el("code", "requirement-meta",
                String(assessment.requirement_id || "") + " · " +
                String(assessment.source_locator || "")));
            list.appendChild(card);
        });
        parent.appendChild(list);
    }

    function renderReview(main) {
        var sem = DATA.semantic || {};
        renderIdentityCompact(main);

        var zone = el("section", "advisory-zone");
        var banner = el("div", "advisory-banner");
        banner.appendChild(el("span", "ab-tag", LANG === "zh" ? "仅供参考" : "Advisory"));
        banner.appendChild(el("span", null, localizedDisclaimer(sem.banner || "")));
        zone.appendChild(banner);

        var body = el("div", "advisory-body");
        body.appendChild(el("h2", null, t("laj_title")));
        body.appendChild(el("p", "advisory-sub", t("laj_sub") + " " + localizedDisclaimer(sem.boundary || "")));
        if (sem.request_template || sem.store_qualified ||
                (sem.compatible_result_options || []).length) {
            renderReaderReviewControl(body, sem);
        }

        if (sem.status === "not_run") {
            var card = el("div", "unavailable-card");
            card.appendChild(el("span", "badge badge-missing", t("laj_not_run")));
            card.appendChild(el("p", null, t("laj_not_run_note")));
            body.appendChild(card);
            zone.appendChild(body);
            main.appendChild(zone);
            return;
        }

        var statusRow = el("p", "laj-status-row");
        statusRow.appendChild(el("span", "fb-k", t("laj_status") + "  "));
        statusRow.appendChild(el("span", "badge badge-advisory", localizedStatus(sem.status || t("unavailable"))));
        body.appendChild(statusRow);

        var cov = sem.coverage || {};
        var stripC = el("div", "coverage-strip");
        [["cov_assessed", cov.assessed_unit_count, "clear"],
         ["cov_findings", cov.finding_count, "attention"],
         ["cov_withheld", cov.withheld_finding_count, "unable"],
         ["cov_abstentions", cov.abstention_count, "evidence"]].forEach(function (c) {
            var chip = el("span", "cov-chip " + c[2]);
            chip.appendChild(el("b", null, String(c[1] == null ? 0 : c[1])));
            chip.appendChild(el("span", null, " " + t(c[0])));
            stripC.appendChild(chip);
        });
        body.appendChild(stripC);

        var profileInventory = renderAssessmentPlanSummary(body, sem);
        if (!profileInventory) {
            body.appendChild(el("h2", null, t("dim_title")));
            var dims = el("div", "dim-strip");
            (sem.dimensions || []).forEach(function (d) {
                var chip = el("div", "dim-chip");
                chip.appendChild(el("span", "dim-name", localizedDimension(d.dimension_id)));
                var reported = d.state === "finding_reported";
                chip.appendChild(el("span",
                    "dim-status " + (reported ? "finding_reported" : "not_assessed_in_view"),
                    t(reported ? "dim_finding_reported" : "dim_not_assessed")));
                dims.appendChild(chip);
            });
            body.appendChild(dims);
        }

        var findings = sem.findings || [];
        findings.forEach(function (f) { body.appendChild(findingCard(f)); });
        renderRequirementAssessments(body, sem);

        var ho = el("p", "handoff-note");
        ho.appendChild(el("strong", null, t("handoff_title") + " · "));
        ho.appendChild(el("span", null, localizedHandoff(sem.handoff_note || "")));
        body.appendChild(ho);

        var rcs = sem.reason_codes || [];
        if (rcs.length) {
            var rcRow = el("p", "laj-status-row");
            rcRow.appendChild(el("span", "fb-k", t("reason_codes_title") + "  "));
            rcs.forEach(function (rc) { rcRow.appendChild(el("span", "badge badge-missing", localizedReasonCode(rc))); });
            body.appendChild(rcRow);
        }

        if (sem.disclaimer) {
            var dis = el("p", "cov-note");
            dis.appendChild(el("strong", null, t("disclaimer_title") + " · "));
            dis.appendChild(el("span", null, localizedDisclaimer(sem.disclaimer)));
            body.appendChild(dis);
        }

        zone.appendChild(body);
        main.appendChild(zone);
    }

    function findingCard(f) {
        var card = el("article", "finding-card");
        var head = el("div", "finding-head");
        head.appendChild(el("span", "f-dim", localizedDimension(f.dimension_id || "")));
        head.appendChild(el("span", "badge badge-advisory", localizedProtocol(f.severity || "")));
        head.appendChild(el("span", "badge badge-info", localizedProtocol(f.impact_scope || "")));
        head.appendChild(el("span", "badge badge-info", localizedScope(f.scope_class || "")));
        if (f.status) head.appendChild(el("span", "badge badge-advisory", localizedStatus(f.status)));
        card.appendChild(head);

        var body = el("div", "finding-body");
        var rows = [
            ["f_unit", f.assessment_unit_id],
            ["f_observation", f.observation],
            ["f_rationale", f.rationale],
            ["f_severity_basis", f.severity_basis],
            ["f_confidence_basis", f.confidence_basis],
            ["f_action", f.recommended_human_action],
            ["f_external_premise", f.external_premise_disclosure]
        ];
        if (f.context_requirement_ids && f.context_requirement_ids.length) {
            rows.push(["f_context_reqs", f.context_requirement_ids.join(", ")]);
        }
        if (f.suggested_rewrite) rows.push(["f_rewrite", f.suggested_rewrite]);
        rows.forEach(function (kv) {
            if (kv[1] == null || kv[1] === "") return;
            var row = el("div", "fb-row");
            row.appendChild(el("span", "fb-k", t(kv[0])));
            row.appendChild(el("span", null, localizedFindingValue(kv[0], kv[1])));
            body.appendChild(row);
        });
        card.appendChild(body);

        var spans = f.report_spans || [];
        if (spans.length) {
            var sp = el("div", "span-list");
            sp.appendChild(el("span", "fb-k", t("f_spans")));
            spans.forEach(function (s) {
                var line = el("div", "span-line");
                line.appendChild(el("code", null,
                    String(s.block_id || "") + "  " +
                    String(s.start_char) + "–" + String(s.end_char)));
                line.appendChild(el("code", null, "excerpt_sha256 " + String(s.excerpt_sha256 || "")));
                line.appendChild(el("code", null, "report_sha256 " + String(s.report_sha256 || "")));
                sp.appendChild(line);
            });
            card.appendChild(sp);
        }

        card.appendChild(el("div", "finding-meta", String(f.finding_id || "")));
        if (ACTION_SESSION && (DATA.semantic || {}).review_actions_available &&
                f.finding_fingerprint) {
            var controls = el("div", "finding-body");
            controls.appendChild(el("strong", null, t("disposition_title")));
            if (f.human_disposition) {
                controls.appendChild(el("p", "section-muted",
                    localizedProtocol(f.human_disposition.decision) + " · " +
                    String(f.human_disposition.disposition_id)));
            }
            var note = document.createElement("textarea");
            note.setAttribute("aria-label", t("disposition_title"));
            note.maxLength = 4000;
            controls.appendChild(note);
            var commandStatus = el("p", "section-muted");
            ["accept", "reject", "defer"].forEach(function (decision) {
                var button = el("button", "qp-tab", t("disposition_" + decision));
                button.type = "button";
                button.addEventListener("click", function () {
                    var request = requestId("human-disposition");
                    if (!request) {
                        commandStatus.textContent = t("command_failed");
                        return;
                    }
                    sendReviewCommand(decision, {
                        schema_version: "briefloop.post_final_finding_disposition_input.v1",
                        human_actor_id: "local-human-reviewer",
                        human_request_id: request,
                        assessment_result_id: DATA.semantic.assessment_result_id,
                        reader_view_sha256: DATA.semantic.reader_view_sha256,
                        finding_id: f.finding_id,
                        finding_fingerprint: f.finding_fingerprint,
                        decision: decision,
                        human_note: note.value || null
                    }, commandStatus);
                });
                controls.appendChild(button);
            });
            if (f.human_disposition && f.human_disposition.decision === "accept") {
                var guidance = document.createElement("textarea");
                guidance.setAttribute("aria-label", t("guidance_edit"));
                guidance.maxLength = 12000;
                controls.appendChild(guidance);
                var save = el("button", "qp-tab", t("guidance_save"));
                save.type = "button";
                save.addEventListener("click", function () {
                    var request = requestId("human-guidance-draft");
                    if (!request || !guidance.value.trim()) {
                        commandStatus.textContent = t("command_failed");
                        return;
                    }
                    sendReviewCommand("draft", {
                        schema_version: "briefloop.post_final_guidance_draft_input.v1",
                        human_actor_id: "local-human-reviewer",
                        human_request_id: request,
                        provenance_kind: "accepted_model_finding",
                        assessment_result_id: DATA.semantic.assessment_result_id,
                        assessment_result_fingerprint: DATA.semantic.assessment_result_fingerprint,
                        finding_id: f.finding_id,
                        finding_fingerprint: f.finding_fingerprint,
                        disposition_id: f.human_disposition.disposition_id,
                        disposition_fingerprint: f.human_disposition.disposition_fingerprint,
                        guidance_text: guidance.value
                    }, commandStatus);
                });
                controls.appendChild(save);
            }
            controls.appendChild(commandStatus);
            card.appendChild(controls);
        }
        return card;
    }

    function observationInput(parent, labelKey, maxLength, type) {
        var label = el("label", "observation-field");
        label.appendChild(el("span", "fb-k", t(labelKey)));
        var input = document.createElement("input");
        input.type = type || "text";
        if (maxLength) input.maxLength = maxLength;
        input.autocomplete = "off";
        input.spellcheck = false;
        label.appendChild(input);
        parent.appendChild(label);
        return input;
    }

    function observationPayload(form) {
        var text = form.text.value.trim();
        if (!text) return null;
        var payload = {
            schema_version: "briefloop.post_final_human_observation_input.v1",
            human_actor_id: "local-human-reviewer",
            human_request_id: form.requestId || requestId("human-observation"),
            observation_text: text
        };
        if (!payload.human_request_id) return null;
        var sem = DATA.semantic || {};
        if (sem.selected_result_id && sem.selected_result_fingerprint &&
                sem.reader_view_sha256) {
            payload.assessment_result_id = sem.selected_result_id;
            payload.assessment_result_fingerprint = sem.selected_result_fingerprint;
            payload.reader_view_sha256 = sem.reader_view_sha256;
        }
        if (form.requirement.value.trim()) payload.requirement_id = form.requirement.value.trim();
        if (form.claim.value.trim()) payload.claim_id = form.claim.value.trim();
        var scope = form.scope.value;
        var dimension = form.dimension.value.trim();
        if (scope || dimension) {
            if (!scope || !dimension) return null;
            payload.scope_class = scope;
            payload.dimension_id = dimension;
        }
        var spanValues = [
            form.spanReport.value.trim(), form.spanBlock.value.trim(),
            form.spanStart.value.trim(), form.spanEnd.value.trim(),
            form.spanExcerpt.value.trim()
        ];
        var spanAny = spanValues.some(function (value) { return Boolean(value); });
        if (spanAny) {
            if (spanValues.some(function (value) { return !value; })) return null;
            var start = Number(form.spanStart.value);
            var end = Number(form.spanEnd.value);
            if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start) {
                return null;
            }
            payload.report_span = {
                schema_version: "briefloop.post_final_human_observation_report_span.v1",
                report_sha256: spanValues[0],
                block_id: spanValues[1],
                start_char: start,
                end_char: end,
                excerpt_sha256: spanValues[4]
            };
        }
        return payload;
    }

    function renderObservationComposer(zone, imp) {
        var card = el("section", "observation-card");
        var pendingRequestId = null;
        card.appendChild(el("h3", null, t("observation_title")));
        card.appendChild(el("p", "section-muted", t("observation_sub")));
        var binding = el("p", "observation-binding");
        binding.appendChild(el("span", "badge badge-info", t("observation_origin")));
        binding.appendChild(el("span", null, " · " +
            (imp.observation_binding_mode === "selected_result" ?
                t("observation_binding_result") : t("observation_binding_report"))));
        card.appendChild(binding);
        var textLabel = el("label", "observation-field");
        textLabel.appendChild(el("span", "fb-k", t("observation_text_label")));
        var text = document.createElement("textarea");
        text.className = "fb-textarea";
        text.maxLength = 12000;
        textLabel.appendChild(text);
        card.appendChild(textLabel);
        var refs = el("details", "observation-refs");
        refs.appendChild(el("summary", null, t("observation_refs")));
        var grid = el("div", "observation-grid");
        var requirement = observationInput(grid, "observation_requirement", 160);
        var claim = observationInput(grid, "observation_claim", 160);
        var scopeLabel = el("label", "observation-field");
        scopeLabel.appendChild(el("span", "fb-k", t("observation_scope")));
        var scope = document.createElement("select");
        scope.appendChild(el("option", null, ""));
        ["O1", "O2"].forEach(function (value) {
            var option = el("option", null, value);
            option.value = value;
            scope.appendChild(option);
        });
        scopeLabel.appendChild(scope);
        grid.appendChild(scopeLabel);
        var dimension = observationInput(grid, "observation_dimension", 160);
        refs.appendChild(grid);
        refs.appendChild(el("p", "section-muted", t("observation_span")));
        var spanGrid = el("div", "observation-grid");
        var spanReport = observationInput(spanGrid, "observation_span_report", 64);
        var spanBlock = observationInput(spanGrid, "observation_span_block", 32);
        var spanStart = observationInput(spanGrid, "observation_span_start", 12, "number");
        var spanEnd = observationInput(spanGrid, "observation_span_end", 12, "number");
        var spanExcerpt = observationInput(spanGrid, "observation_span_excerpt", 64);
        refs.appendChild(spanGrid);
        card.appendChild(refs);
        var status = el("p", "reader-review-command-status");
        var submit = el("button", "btn-primary", t("observation_submit"));
        submit.type = "button";
        submit.addEventListener("click", function () {
            if (!pendingRequestId) pendingRequestId = requestId("human-observation");
            var payload = observationPayload({
                text: text,
                requestId: pendingRequestId,
                requirement: requirement,
                claim: claim,
                scope: scope,
                dimension: dimension,
                spanReport: spanReport,
                spanBlock: spanBlock,
                spanStart: spanStart,
                spanEnd: spanEnd,
                spanExcerpt: spanExcerpt
            });
            if (!payload) {
                status.textContent = t("observation_invalid_refs");
                return;
            }
            sendReviewCommand("append_observation", payload, status, submit,
                function () { pendingRequestId = null; });
        });
        card.appendChild(submit);
        card.appendChild(status);
        zone.appendChild(card);
    }

    function renderObservationHistory(zone, observations) {
        var wrap = el("div", "observation-history");
        wrap.appendChild(el("h3", null, t("observation_history")));
        if (!observations.length) {
            wrap.appendChild(el("p", "section-muted", t("observation_none")));
            zone.appendChild(wrap);
            return;
        }
        observations.forEach(function (row) {
            if (!row || typeof row !== "object") return;
            var entry = el("article", "rec-entry observation-entry");
            var head = el("div", "re-head");
            head.appendChild(el("span", "badge badge-info", t("observation_origin")));
            head.appendChild(el("span", "badge badge-advisory", localizedStatus(row.status || "recorded")));
            entry.appendChild(head);
            entry.appendChild(el("div", "re-text", String(
                row.observation_text || row.observation || "")));
            var meta = el("div", "re-meta");
            var refs = [];
            ["observation_id", "observation_fingerprint", "requirement_id", "claim_id",
             "report_span_id", "dimension_id", "scope_class", "recorded_at"].forEach(function (key) {
                if (row[key] != null) refs.push(key + "=" + String(row[key]));
            });
            meta.textContent = refs.join(" · ");
            entry.appendChild(meta);
            if (ACTION_SESSION && row.observation_id && row.observation_fingerprint &&
                    row.status !== "superseded") {
                var controls = el("div", "observation-actions");
                var guidanceText = document.createElement("textarea");
                guidanceText.className = "fb-textarea";
                guidanceText.maxLength = 12000;
                guidanceText.setAttribute("aria-label", t("observation_guidance"));
                controls.appendChild(guidanceText);
                var guidanceButton = el("button", "btn-ghost", t("observation_guidance"));
                guidanceButton.type = "button";
                var commandStatus = el("p", "reader-review-command-status");
                guidanceButton.addEventListener("click", function () {
                    var request = requestId("human-guidance-draft");
                    if (!request || !guidanceText.value.trim()) {
                        commandStatus.textContent = t("command_failed");
                        return;
                    }
                    var sem = DATA.semantic || {};
                    sendReviewCommand("draft", {
                        schema_version: "briefloop.post_final_guidance_draft_input.v1",
                        human_actor_id: "local-human-reviewer",
                        human_request_id: request,
                        provenance_kind: "human_observation",
                        assessment_result_id: sem.selected_result_id || null,
                        assessment_result_fingerprint: sem.selected_result_fingerprint || null,
                        observation_id: row.observation_id,
                        observation_fingerprint: row.observation_fingerprint,
                        guidance_text: guidanceText.value.trim()
                    }, commandStatus, guidanceButton);
                });
                controls.appendChild(guidanceButton);
                var replacementText = document.createElement("textarea");
                replacementText.className = "fb-textarea";
                replacementText.maxLength = 12000;
                replacementText.value = String(row.observation_text || row.observation || "");
                replacementText.setAttribute("aria-label", t("observation_supersede_text"));
                controls.appendChild(replacementText);
                var supersede = el("button", "btn-ghost", t("observation_supersede"));
                supersede.type = "button";
                supersede.addEventListener("click", function () {
                    var request = requestId("human-observation-supersede");
                    if (!request) {
                        commandStatus.textContent = t("command_failed");
                        return;
                    }
                    var sem = DATA.semantic || {};
                    var replacementPayload = {
                        schema_version: "briefloop.post_final_human_observation_supersede_input.v1",
                        human_actor_id: "local-human-reviewer",
                        human_request_id: request,
                        observation_text: replacementText.value.trim(),
                        previous_observation_id: row.observation_id,
                        previous_observation_fingerprint: row.observation_fingerprint
                    };
                    if (row.assessment_result_id && row.assessment_result_fingerprint &&
                            row.reader_view_sha256) {
                        replacementPayload.assessment_result_id = row.assessment_result_id;
                        replacementPayload.assessment_result_fingerprint = row.assessment_result_fingerprint;
                        replacementPayload.reader_view_sha256 = row.reader_view_sha256;
                    } else if (sem.selected_result_id && sem.selected_result_fingerprint &&
                            sem.reader_view_sha256) {
                        replacementPayload.assessment_result_id = sem.selected_result_id;
                        replacementPayload.assessment_result_fingerprint = sem.selected_result_fingerprint;
                        replacementPayload.reader_view_sha256 = sem.reader_view_sha256;
                    }
                    ["requirement_id", "claim_id", "scope_class", "dimension_id"].forEach(function (key) {
                        if (row[key] != null) replacementPayload[key] = row[key];
                    });
                    if (row.report_span && typeof row.report_span === "object") {
                        replacementPayload.report_span = row.report_span;
                    }
                    sendReviewCommand("supersede_observation", {
                        schema_version: replacementPayload.schema_version,
                        human_actor_id: replacementPayload.human_actor_id,
                        human_request_id: replacementPayload.human_request_id,
                        observation_text: replacementPayload.observation_text,
                        previous_observation_id: replacementPayload.previous_observation_id,
                        previous_observation_fingerprint: replacementPayload.previous_observation_fingerprint,
                        assessment_result_id: replacementPayload.assessment_result_id,
                        assessment_result_fingerprint: replacementPayload.assessment_result_fingerprint,
                        reader_view_sha256: replacementPayload.reader_view_sha256,
                        requirement_id: replacementPayload.requirement_id,
                        claim_id: replacementPayload.claim_id,
                        report_span: replacementPayload.report_span,
                        scope_class: replacementPayload.scope_class,
                        dimension_id: replacementPayload.dimension_id
                    }, commandStatus, supersede);
                });
                controls.appendChild(supersede);
                controls.appendChild(commandStatus);
                entry.appendChild(controls);
            }
            wrap.appendChild(entry);
        });
        zone.appendChild(wrap);
    }

    function successorRunId(predecessor) {
        var now = new Date();
        var stamp = now.toISOString().replace(/[-:TZ.]/g, "").slice(0, 14);
        var suffix = requestId("run") || "run-manual";
        return "successor-" + stamp + "-" + suffix.slice(-8);
    }

    function renderSuccessorComposer(zone, successor) {
        var card = el("section", "successor-card");
        card.appendChild(el("h3", null, t("successor_title")));
        card.appendChild(el("p", "section-muted", t("successor_sub")));
        var direction = successor.run_direction || {};
        var directionDetails = el("details", "successor-direction");
        directionDetails.open = false;
        directionDetails.appendChild(el("summary", null, t("successor_direction")));
        var directionRows = el("dl", "successor-direction-list");
        [
            ["subject_name", direction.subject_name],
            ["brief_title", direction.brief_title],
            ["task_objective", direction.task_objective],
            ["audience", direction.audience],
            ["output_language", direction.output_language],
            ["source_profile", direction.source_profile],
            ["report_date", direction.report_date]
        ].forEach(function (pair) {
            if (pair[1] == null) return;
            directionRows.appendChild(el("dt", null, pair[0]));
            directionRows.appendChild(el("dd", null, String(pair[1])));
        });
        directionDetails.appendChild(directionRows);
        card.appendChild(directionDetails);

        var approved = successor.approved_guidance || [];
        var guidance = el("div", "successor-guidance");
        guidance.appendChild(el("strong", null, t("successor_guidance")));
        if (!approved.length) {
            guidance.appendChild(el("p", "section-muted", t("successor_no_guidance")));
        } else {
            var list = el("ul", "successor-guidance-list");
            approved.forEach(function (item) {
                var li = el("li");
                li.appendChild(el("span", "badge badge-advisory", localizedProtocol(item.guidance_scope || "guidance")));
                li.appendChild(el("span", null, String(item.guidance_text || "")));
                list.appendChild(li);
            });
            guidance.appendChild(list);
        }
        card.appendChild(guidance);

        var fields = el("div", "successor-fields");
        var idLabel = el("label", "observation-field");
        idLabel.appendChild(el("span", "fb-k", t("successor_run_id")));
        var idInput = document.createElement("input");
        idInput.type = "text";
        idInput.maxLength = 160;
        idInput.autocomplete = "off";
        idInput.value = successorRunId(successor.predecessor_run_id);
        idLabel.appendChild(idInput);
        fields.appendChild(idLabel);
        var choice = el("div", "successor-choice");
        var excludeLabel = el("label", "reader-review-confirmation");
        var exclude = document.createElement("input");
        exclude.type = "radio";
        exclude.name = "successor-guidance-choice";
        exclude.value = "exclude";
        exclude.checked = successor.include_default !== true;
        excludeLabel.appendChild(exclude);
        excludeLabel.appendChild(el("span", null, t("successor_exclude")));
        choice.appendChild(excludeLabel);
        var includeLabel = el("label", "reader-review-confirmation");
        var include = document.createElement("input");
        include.type = "radio";
        include.name = "successor-guidance-choice";
        include.value = "include";
        include.checked = successor.include_default === true;
        include.disabled = approved.length === 0;
        includeLabel.appendChild(include);
        includeLabel.appendChild(el("span", null, t("successor_include")));
        choice.appendChild(includeLabel);
        fields.appendChild(choice);
        card.appendChild(fields);

        var status = el("p", "reader-review-command-status");
        var start = el("button", "btn-primary", t("successor_start"));
        start.type = "button";
        start.addEventListener("click", function () {
            var runId = String(idInput.value || "").trim();
            if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/.test(runId) ||
                    !successor.run_direction) {
                status.textContent = t("successor_id_required");
                return;
            }
            sendReviewCommand("start_successor", {
                schema_version: "briefloop.post_final_successor_start_input.v1",
                successor_run_id: runId,
                run_direction: successor.run_direction,
                include_approved_guidance: include.checked
            }, status, start);
        });
        card.appendChild(start);
        card.appendChild(status);
        zone.appendChild(card);
    }

    /* ---- feedback tab: read-only exports show history; loopback sessions add commands ---- */
    function renderFeedback(main) {
        var imp = DATA.improvement || {};
        renderIdentityCompact(main);

        var zone = el("section", "feedback-zone");
        zone.appendChild(el("h2", null, t("fb_title")));
        zone.appendChild(el("p", "feedback-sub",
            t(imp.status === "available" ? "fb_available_sub" : "fb_sub")));

        if (ACTION_SESSION && imp.observation_allowed === true) {
            renderObservationComposer(zone, imp);
        } else if (ACTION_SESSION && imp.observation_allowed !== true) {
            var unavailableObservation = el("p", "section-muted", t("observation_not_allowed"));
            zone.appendChild(unavailableObservation);
        }
        renderObservationHistory(zone, imp.human_observations || []);

        var wrap = el("div", "recorded-list");
        wrap.appendChild(el("h3", null, t("recorded_title")));
        var recorded = imp.recorded || [];
        var latestDraftByGuidance = {};
        recorded.forEach(function (row) {
            if (typeof row !== "object" || !row.guidance_id) return;
            var revision = Number(row.draft_revision || 0);
            latestDraftByGuidance[row.guidance_id] = Math.max(
                latestDraftByGuidance[row.guidance_id] || 0,
                revision
            );
        });
        if (!recorded.length) {
            wrap.appendChild(el("p", "section-muted", t("recorded_none")));
        } else {
            recorded.forEach(function (r) {
                var entry = el("div", "rec-entry");
                entry.appendChild(el("div", "re-text", typeof r === "string" ? r : JSON.stringify(r)));
                if (ACTION_SESSION && typeof r === "object" && r.guidance_id &&
                        Number(r.draft_revision) === latestDraftByGuidance[r.guidance_id]) {
                    var commandStatus = el("p", "section-muted");
                    var actionLabels = {
                        approve: "guidance_approve",
                        deactivate: "guidance_deactivate",
                        revert: "guidance_revert",
                        supersede: "guidance_supersede"
                    };
                    (r.legal_actions || []).forEach(function (actionName) {
                        var action = [actionName, actionLabels[actionName]];
                        if (!action[1]) return;
                        var button = el("button", "qp-tab", t(action[1]));
                        button.type = "button";
                        button.addEventListener("click", function () {
                            var request = requestId("human-guidance-status");
                            if (!request) {
                                commandStatus.textContent = t("command_failed");
                                return;
                            }
                            sendReviewCommand(action[0], {
                                schema_version: "briefloop.post_final_guidance_status_input.v1",
                                human_actor_id: "local-human-reviewer",
                                human_request_id: request,
                                guidance_id: r.guidance_id,
                                draft_revision: r.draft_revision
                            }, commandStatus);
                        });
                        entry.appendChild(button);
                    });
                    entry.appendChild(commandStatus);
                }
                wrap.appendChild(entry);
            });
        }
        zone.appendChild(wrap);

        if (imp.status !== "available") {
            var card = el("div", "unavailable-card");
            card.appendChild(el("span", "badge badge-missing", localizedStatus(imp.status || t("unavailable"))));
            if (imp.reason_code) card.appendChild(el("code", null, String(imp.reason_code)));
            card.appendChild(el("p", null, t("il_unavailable")));
            zone.appendChild(card);
        }

        var n = el("p", "consumption-note");
        n.appendChild(el("strong", null, t("consumption_label")));
        n.appendChild(el("span", null, String(imp.consumption_note || "")));
        zone.appendChild(n);

        var planned = el("p", "planned-note");
        planned.appendChild(el("span", "badge badge-missing", t("planned_label")));
        planned.appendChild(el("span", null, " " + String(imp.planned_note || "")));
        zone.appendChild(planned);

        var successor = DATA.successor || {};
        if (ACTION_SESSION && successor.available === true) {
            renderSuccessorComposer(zone, successor);
        }

        main.appendChild(zone);
    }

    /* ---- tabs ---- */
    var TABS = [
        ["brief", "tab_brief"],
        ["quality", "tab_quality"],
        ["review", "tab_review"],
        ["feedback", "tab_feedback"]
    ];

    function renderTabBar(main) {
        var bar = el("nav", "qp-tabs");
        bar.setAttribute("aria-label", t("tab_aria"));
        TABS.forEach(function (tb) {
            var btn = el("button", "qp-tab" + (STATE.tab === tb[0] ? " active" : ""), t(tb[1]));
            btn.type = "button";
            btn.dataset.tab = tb[0];
            btn.setAttribute("aria-selected", STATE.tab === tb[0] ? "true" : "false");
            if (tb[0] === "review") {
                var sem = DATA.semantic || {};
                var n = ((sem.coverage || {}).finding_count) || 0;
                if (sem.status !== "not_run" && n > 0) {
                    btn.appendChild(el("span", "tab-badge advisory", String(n)));
                }
            }
            btn.addEventListener("click", function () { switchTab(tb[0]); });
            bar.appendChild(btn);
        });
        main.appendChild(bar);
    }

    function switchTab(id) {
        if (TABS.every(function (tb) { return tb[0] !== id; })) return;
        STATE.tab = id;
        if (!ACTION_SESSION) {
            try { location.hash = id; } catch (e) { /* file:// quirks */ }
        }
        renderAll();
        window.scrollTo(0, 0);
    }

    function renderFooter(main) {
        var f = el("footer", "qp-footer");
        f.appendChild(el("p", null,
            ACTION_SESSION ? t("session_badge") : t("footer_boundary")));
        var p = el("p");
        p.appendChild(el("code", null, String(DATA.schema_version || "")));
        f.appendChild(p);
        main.appendChild(f);
    }

    function renderAll() {
        var main = document.getElementById("qp-main");
        main.replaceChildren();
        if (!DATA) {
            main.appendChild(el("p", "data-error", t("data_error")));
            return;
        }
        renderTabBar(main);
        if (STATE.tab === "brief") renderBrief(main);
        else if (STATE.tab === "quality") renderQuality(main);
        else if (STATE.tab === "review") renderReview(main);
        else renderFeedback(main);
        renderFooter(main);
    }

    /* ---- language ---- */
    var langBtn = document.getElementById("btn-lang-toggle");
    var langMenu = document.getElementById("lang-menu");
    function syncLanguageChrome() {
        document.documentElement.lang = LANG === "en" ? "en" : "zh-CN";
        langMenu.querySelectorAll("li").forEach(function (item) {
            var selected = item.dataset.lang === LANG;
            item.setAttribute("aria-selected", selected ? "true" : "false");
            if (selected) {
                document.getElementById("lang-current").textContent = item.textContent;
            }
        });
        document.querySelectorAll("[data-i18n]").forEach(function (node) {
            var key = node.dataset.i18n;
            node.textContent = t(
                ACTION_SESSION && key === "top_badge" ? "session_badge" : key
            );
        });
    }
    langBtn.addEventListener("click", function () {
        var open = !langMenu.hidden;
        langMenu.hidden = open;
        langBtn.setAttribute("aria-expanded", open ? "false" : "true");
    });
    langMenu.querySelectorAll("li").forEach(function (li) {
        li.addEventListener("click", function () {
            LANG = li.dataset.lang;
            syncLanguageChrome();
            langMenu.hidden = true;
            langBtn.setAttribute("aria-expanded", "false");
            renderAll();
        });
    });

    /* ---- boot ---- */
    var initialHash = "";
    if (!ACTION_SESSION) {
        try { initialHash = location.hash.replace("#", ""); } catch (e) { /* ignore */ }
        if (TABS.some(function (tb) { return tb[0] === initialHash; })) STATE.tab = initialHash;
    }
    syncLanguageChrome();
    window.addEventListener("hashchange", function () {
        if (ACTION_SESSION) return;
        var h = "";
        try { h = location.hash.replace("#", ""); } catch (e) { /* ignore */ }
        if (TABS.some(function (tb) { return tb[0] === h; }) && h !== STATE.tab) {
            STATE.tab = h;
            renderAll();
        }
    });
    renderAll();
})();
