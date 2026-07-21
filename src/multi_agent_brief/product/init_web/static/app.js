/* ==========================================================================
   BriefLoop init_web — one-shot workspace wizard (production static asset)
   Derived (MIT) from the BriefLoop interactive-init prototype; upstream fork
   shape: PPT Master confirm_ui @619a954 (MIT).
   Served under CSP script-src 'self'; style-src 'self': no inline handlers,
   no inline style attributes (custom accent goes through CSSOM insertRule on
   the same-origin stylesheet). DOM via createElement + textContent only;
   clearing uses replaceChildren().
   Real submission: POST /api/v1/submit with the session token parsed from the
   URL fragment (then stripped via history.replaceState).
   ========================================================================== */
(function () {
    "use strict";

    /* ---- session token from URL fragment; strip it from the address bar ---- */
    var SESSION = { token: "", sessionId: "" };
    (function parseSessionFromHash() {
        var hash = "";
        try { hash = location.hash || ""; } catch (e) { return; }
        if (hash.charAt(0) === "#") hash = hash.slice(1);
        if (!hash) return;
        hash.split("&").forEach(function (pair) {
            var idx = pair.indexOf("=");
            if (idx < 0) return;
            var key = pair.slice(0, idx);
            var value = pair.slice(idx + 1);
            if (key === "token") SESSION.token = decodeURIComponent(value);
            if (key === "session") SESSION.sessionId = decodeURIComponent(value);
        });
        try {
            history.replaceState(null, "", location.pathname + location.search);
        } catch (e) { /* non-critical */ }
    })();

    /* ---- i18n ---- */
    var MESSAGES = {
        zh: {
            panel_title: "创建报告工作区",
            top_banner: "一次性初始化向导 · 预览为固定合成内容",
            step_1: "报告方向",
            step_2: "交付与版式",
            step_3: "预览与确认",
            btn_back: "← 上一步",
            btn_next: "下一步 →",
            btn_confirm: "创建报告工作区",
            sec_company: "公司 / 组织（必填）",
            placeholder_company: "例如：ACME 新能源",
            sec_report_type: "你在做什么报告？",
            sec_audience: "谁会读它？",
            sec_purpose: "它要支撑什么决策？（必填）",
            sec_brief_title: "简报标题（可选）",
            placeholder_brief_title: "例如：组件价格周报",
            sec_cadence: "更新频率",
            sec_window: "时间窗",
            sec_language: "输出语言",
            sec_source: "来源姿态",
            sec_freetext: "用一句话描述你的需求（可选）",
            note_freetext: "Agent 会把它解释成下面的结构化选项；解释只是建议，确认后才生效。",
            placeholder_purpose: "例如：跟踪组件价格，支撑采购议价",
            placeholder_freetext: "例如：给董事会看，十分钟读完，保留完整数据表，同时给我 DOCX 和网页版。",
            placeholder_window: "例如：近 14 天",
            sec_formats: "读者收到什么文件？（可多选）",
            sec_presentation: "版式风格",
            custom_base_title: "基底风格",
            custom_base_note: "自定义 = 基底风格 + 有界覆盖（密度、强调色等为独立字段）。",
            sec_density: "信息密度",
            sec_tables: "数据表处理",
            sec_citations: "来源与引用展示",
            sec_accent: "品牌强调色（可选，仅装饰）",
            placeholder_hex: "例如 #2f5d8a",
            hex_err_format: "格式无效：请输入 #RGB 或 #RRGGBB。",
            hex_err_contrast: "太浅：与纸面对比不足。",
            hex_err_semantic: "与状态色（绿/红/黄/紫）冲突：强调色只作装饰，请换色相。",
            hex_ok: "已应用（仅装饰，不影响状态色）。",
            recommended_det: "推荐",
            recommended_agent: "Agent 建议",
            interp_title: "Agent 解释（仅供参考 · 需确认）",
            interp_unresolved: "无法映射（确认前不生效）",
            interp_note: "以上映射只是建议：在第 3 步逐条接受或丢弃后，才会进入最终请求。",
            review_explicit: "你明确选择的内容",
            review_proposed: "来自文字的提议 · 待你处置",
            review_unresolved: "未解决 · 不生效",
            review_path_k: "工作区位置",
            review_statement: "确认后，将在此位置创建 fresh-v2 工作区：一次确定性事务提交报告方向与交付选择，并返回 ControlStore 收据。",
            review_accept: "接受",
            review_discard: "丢弃",
            review_accepted: "已接受",
            review_discarded: "已丢弃（不生效）",
            review_none: "（无）",
            field_company: "公司 / 组织",
            field_brief_title: "简报标题",
            field_report_type: "报告类型",
            field_audience: "读者",
            field_purpose: "用途",
            field_cadence: "频率",
            field_window: "时间窗",
            field_language: "语言",
            field_source: "来源姿态",
            field_formats: "交付格式",
            field_presentation: "版式风格",
            field_density: "信息密度",
            field_tables: "数据表",
            field_citations: "引用展示",
            field_accent: "强调色",
            field_freetext: "原始文字",
            err_required: "还有必选项未完成：",
            err_pending: "请先处置每条 Agent 提议（接受或丢弃）。",
            err_session: "缺少会话令牌：请使用初始化命令给出的完整链接打开本页。",
            status_ready: "可以确认创建。",
            status_fill: "完成必选项后即可创建。",
            preview_fidelity_html: "HTML 为示意预览",
            preview_fidelity_docx: "DOCX 为近似版式预览",
            preview_formats: "交付：",
            cf_committed: "已提交 · committed",
            cf_replayed: "重放 · replayed",
            cf_replayed_badge: "replayed · 无新写入",
            cf_conflict: "冲突 · submission_replay_conflict",
            cf_error: "提交被拒绝",
            cf_sub_committed: "报告工作区已初始化。以下是 ControlStore 事务返回的真实收据：",
            cf_sub_replayed: "相同请求已提交过——返回原收据，不产生第二个工作区。",
            cf_sub_conflict: "同一 request_id 提交了不同内容。已拒绝，零写入。",
            cf_sub_error: "服务端拒绝了本次提交，未产生任何写入。原因码如上所示。",
            cf_submitting: "正在提交……",
            cf_next: "下一步：",
            cf_again: "再次提交同一请求（replay）",
            cf_close: "关闭，修改后重试",
            cf_note: "此收据由 ControlStore 事务确定性返回；重放返回原收据，冲突请求零写入。"
        },
        en: {
            panel_title: "Create report workspace",
            top_banner: "One-shot init wizard · fixed synthetic preview",
            step_1: "Direction",
            step_2: "Delivery & style",
            step_3: "Review & confirm",
            btn_back: "← Back",
            btn_next: "Next →",
            btn_confirm: "Create report workspace",
            sec_company: "Company / organization (required)",
            placeholder_company: "e.g. ACME Renewables",
            sec_report_type: "What report are you making?",
            sec_audience: "Who will read it?",
            sec_purpose: "What decision should it support? (required)",
            sec_brief_title: "Brief title (optional)",
            placeholder_brief_title: "e.g. Module price weekly",
            sec_cadence: "Cadence",
            sec_window: "Time window",
            sec_language: "Output language",
            sec_source: "Source posture",
            sec_freetext: "Describe what you need (optional)",
            note_freetext: "An agent maps this to the structured choices below; the mapping is advisory until you confirm it.",
            placeholder_purpose: "e.g. track component prices to support procurement",
            placeholder_freetext: "e.g. for the board, a ten-minute read, keep full data tables, and give me both DOCX and a web page.",
            placeholder_window: "e.g. last 14 days",
            sec_formats: "What should readers receive? (multi-select)",
            sec_presentation: "Presentation style",
            custom_base_title: "Base style",
            custom_base_note: "Custom = base style + bounded overrides (density, accent — separate fields).",
            sec_density: "Information density",
            sec_tables: "Table treatment",
            sec_citations: "Source & citation display",
            sec_accent: "Brand accent (optional, decorative only)",
            placeholder_hex: "e.g. #2f5d8a",
            hex_err_format: "Invalid format: use #RGB or #RRGGBB.",
            hex_err_contrast: "Too light: insufficient contrast on paper.",
            hex_err_semantic: "Conflicts with a status color (green/red/amber/purple); accent is decorative only — pick another hue.",
            hex_ok: "Applied (decorative only; status colors unaffected).",
            recommended_det: "Recommended",
            recommended_agent: "Agent pick",
            interp_title: "Agent interpretation (advisory · needs confirmation)",
            interp_unresolved: "Unmapped (no effect until resolved)",
            interp_note: "These mappings are proposals only: accept or discard each one in step 3 before they enter the request.",
            review_explicit: "Your explicit selections",
            review_proposed: "Proposed from your text · awaiting disposition",
            review_unresolved: "Unresolved · no effect",
            review_path_k: "Workspace location",
            review_statement: "Confirming creates a fresh-v2 workspace here: one deterministic transaction commits the direction and delivery choices and returns a ControlStore receipt.",
            review_accept: "Accept",
            review_discard: "Discard",
            review_accepted: "Accepted",
            review_discarded: "Discarded (no effect)",
            review_none: "(none)",
            field_company: "Company / organization",
            field_brief_title: "Brief title",
            field_report_type: "Report type",
            field_audience: "Audience",
            field_purpose: "Purpose",
            field_cadence: "Cadence",
            field_window: "Time window",
            field_language: "Language",
            field_source: "Source posture",
            field_formats: "Formats",
            field_presentation: "Style",
            field_density: "Density",
            field_tables: "Tables",
            field_citations: "Citations",
            field_accent: "Accent",
            field_freetext: "Raw text",
            err_required: "Missing required choices: ",
            err_pending: "Dispose of every agent proposal first (accept or discard).",
            err_session: "Missing session token: open this page via the full link printed by the init command.",
            status_ready: "Ready to create.",
            status_fill: "Complete the required choices to continue.",
            preview_fidelity_html: "HTML preview is representative",
            preview_fidelity_docx: "DOCX preview is approximate",
            preview_formats: "Delivery: ",
            cf_committed: "Committed",
            cf_replayed: "Replayed",
            cf_replayed_badge: "replayed · no new writes",
            cf_conflict: "submission_replay_conflict",
            cf_error: "Submission rejected",
            cf_sub_committed: "The workspace is initialized. Real receipt returned by the ControlStore transaction:",
            cf_sub_replayed: "This exact request was already committed — original receipt returned, no second workspace.",
            cf_sub_conflict: "Same request_id with a different payload. Rejected with zero writes.",
            cf_sub_error: "The server rejected this submission; nothing was written. The reason code is shown above.",
            cf_submitting: "Submitting…",
            cf_next: "Next: ",
            cf_again: "Resubmit the same request (replay)",
            cf_close: "Close, change something, retry",
            cf_note: "This receipt is returned deterministically by a ControlStore transaction; replays return the original receipt and conflicting requests write nothing."
        }
    };

    /* ---- SetupCatalog (BriefLoop product catalog) ---- */
    var CATALOG = {
        report_types: [
            { id: "industry_weekly", zh: ["行业周报", "跟踪行业动向与竞品"], en: ["Industry weekly", "Track industry and competitors"] },
            { id: "management_monthly", zh: ["管理层月报", "月度经营与决策简报"], en: ["Management monthly", "Monthly operating brief"] },
            { id: "document_review", zh: ["文档证据审阅", "出处与合规复核"], en: ["Document review", "Evidence and source review"] }
        ],
        audiences: [
            { id: "board_executive", zh: ["董事会 / 高管", "决策导向，快速阅读"], en: ["Board / executive", "Decision-oriented, fast read"] },
            { id: "management", zh: ["部门管理层", "经营复盘与行动"], en: ["Management", "Review and actions"] },
            { id: "analysts", zh: ["分析团队", "密度、方法与来源"], en: ["Analyst team", "Density, method, sources"] },
            { id: "custom_audience", zh: ["其他（自定义）", ""], en: ["Other (custom)", ""] }
        ],
        cadences: [
            { id: "weekly", zh: ["每周", ""], en: ["Weekly", ""] },
            { id: "monthly", zh: ["每月", ""], en: ["Monthly", ""] },
            { id: "one_time", zh: ["一次性审阅", ""], en: ["One-time review", ""] }
        ],
        windows: [
            { id: "7d", zh: ["近 7 天", ""], en: ["Last 7 days", ""] },
            { id: "30d", zh: ["近 30 天", ""], en: ["Last 30 days", ""] },
            { id: "quarter", zh: ["本季度", ""], en: ["This quarter", ""] },
            { id: "custom_window", zh: ["自定义…", ""], en: ["Custom…", ""] }
        ],
        languages: [
            { id: "zh-CN", zh: ["中文", ""], en: ["Chinese", ""] },
            { id: "en", zh: ["English", ""], en: ["English", ""] },
            { id: "bilingual", zh: ["中英对照", ""], en: ["Bilingual", ""] }
        ],
        sources: [
            { id: "public_web", zh: ["公开网页（已登记）", "轻量公开来源"], en: ["Public web (registered)", "Light public sources"] },
            { id: "local_only", zh: ["仅本地材料", "离线，不上网"], en: ["Local material only", "Offline"] },
            { id: "mixed", zh: ["本地 + 公开网页", ""], en: ["Local + public web", ""] }
        ],
        formats: [
            { id: "docx", zh: ["DOCX", "可打印、可批注"], en: ["DOCX", "Printable, annotatable"] },
            { id: "html", zh: ["网页版 HTML", "浏览器阅读，自包含"], en: ["HTML page", "Self-contained web reading"] },
            { id: "markdown", zh: ["Markdown", "可移植、给下游工具"], en: ["Markdown", "Portable, for downstream tools"] }
        ],
        presentations: [
            { id: "executive_brief", zh: ["高管简报", "紧凑、可扫读、决策导向"], en: ["Executive Brief", "Compact, scannable, decision-first"] },
            { id: "research_note", zh: ["研究报告", "更密的分析、表格与来源"], en: ["Research Note", "Denser analysis, tables, sources"] },
            { id: "formal_internal_report", zh: ["正式内部报告", "规整、正式、内部评审"], en: ["Formal Internal Report", "Controlled, formal, internal review"] },
            { id: "custom_style", zh: ["自定义（有界）", "选基底风格 + 少量覆盖"], en: ["Custom (bounded)", "Base style + bounded overrides"] }
        ],
        densities: [
            { id: "compact", zh: ["紧凑", "十分钟读完"], en: ["Compact", "A ten-minute read"] },
            { id: "balanced", zh: ["均衡", ""], en: ["Balanced", ""] },
            { id: "detailed", zh: ["详尽", "保留方法与细节"], en: ["Detailed", "Method and detail kept"] }
        ],
        tables: [
            { id: "preserve_full", zh: ["保留完整数据表", ""], en: ["Keep full tables", ""] },
            { id: "key_only", zh: ["只保留关键数字", ""], en: ["Key figures only", ""] },
            { id: "prose", zh: ["转为文字叙述", ""], en: ["Convert to prose", ""] }
        ],
        citations: [
            { id: "footnote", zh: ["页内脚注", ""], en: ["Footnotes", ""] },
            { id: "appendix", zh: ["文末来源附录", ""], en: ["Source appendix", ""] },
            { id: "inline", zh: ["行内标注", ""], en: ["Inline markers", ""] }
        ],
        accents: [
            { id: "forest", zh: ["墨绿（默认）", ""], en: ["Forest (default)", ""] },
            { id: "slate", zh: ["青灰蓝", ""], en: ["Slate blue", ""] },
            { id: "umber", zh: ["赭石", ""], en: ["Umber", ""] },
            { id: "graphite", zh: ["石墨", ""], en: ["Graphite", ""] },
            { id: "custom_hex", zh: ["自定义 HEX", ""], en: ["Custom HEX", ""] }
        ]
    };

    /* deterministic recommendation rules (blue badge) */
    var DET_REC = {
        industry_weekly: { presentation: "research_note", formats: ["html", "docx"], density: "balanced" },
        management_monthly: { presentation: "executive_brief", formats: ["docx", "html"], density: "compact" },
        document_review: { presentation: "formal_internal_report", formats: ["docx"], density: "detailed" }
    };

    /* ---- free-text interpretation (deterministic keyword mapper, advisory) ---- */
    var INTERP_RULES = [
        { re: /董事会|高管|board|executive/i, field: "audience", value: "board_executive", labelKey: "field_audience" },
        { re: /管理层|management/i, field: "audience", value: "management", labelKey: "field_audience" },
        { re: /十分钟|快速|简洁|compact|ten.?minute/i, field: "density", value: "compact", labelKey: "field_density" },
        { re: /详尽|详细|detailed/i, field: "density", value: "detailed", labelKey: "field_density" },
        { re: /数据表|表格|tables?/i, field: "tables", value: "preserve_full", labelKey: "field_tables" },
        { re: /网页|web|html|浏览器/i, field: "formats", value: "html", labelKey: "field_formats" },
        { re: /docx|word/i, field: "formats", value: "docx", labelKey: "field_formats" },
        { re: /markdown|md/i, field: "formats", value: "markdown", labelKey: "field_formats" },
        { re: /每周|weekly/i, field: "cadence", value: "weekly", labelKey: "field_cadence" },
        { re: /每月|monthly/i, field: "cadence", value: "monthly", labelKey: "field_cadence" }
    ];

    function interpretFreeText(text) {
        var mapped = [], seen = {};
        INTERP_RULES.forEach(function (rule) {
            var m = text.match(rule.re);
            if (m && !seen[rule.field + ":" + rule.value]) {
                seen[rule.field + ":" + rule.value] = true;
                mapped.push({ from: m[0], field: rule.field, value: rule.value, labelKey: rule.labelKey });
            }
        });
        var unresolved = [];
        var clauses = text.split(/[，,。.;；]+/).map(function (s) { return s.trim(); }).filter(Boolean);
        clauses.forEach(function (c) {
            var hit = INTERP_RULES.some(function (r) { return r.re.test(c); });
            if (!hit) unresolved.push(c);
        });
        return { mapped: mapped, unresolved: unresolved };
    }

    /* ---- bounded custom accent validation (Visual System v1 §21 matrix) ---- */
    function normalizeHex(v) {
        var m = String(v || "").trim().match(/^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/);
        if (!m) return null;
        var h = m[1];
        if (h.length === 3) h = h.split("").map(function (c) { return c + c; }).join("");
        return "#" + h.toLowerCase();
    }
    function hexToRgb(h) {
        return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
    }
    function relLuminance(rgb) {
        var c = rgb.map(function (v) {
            v = v / 255;
            return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
        });
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
    }
    function hueOf(rgb) {
        var r = rgb[0] / 255, g = rgb[1] / 255, b = rgb[2] / 255;
        var max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
        if (d === 0) return 0;
        var h;
        if (max === r) h = ((g - b) / d) % 6;
        else if (max === g) h = (b - r) / d + 2;
        else h = (r - g) / d + 4;
        h = Math.round(h * 60);
        return h < 0 ? h + 360 : h;
    }
    /* returns null when usable, else a message key */
    function accentVerdict(hex) {
        var rgb = hexToRgb(hex);
        var L1 = relLuminance(rgb), L2 = relLuminance([250, 249, 246]);
        var cr = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
        if (cr < 3) return "hex_err_contrast";
        var hue = hueOf(rgb);
        var zones = [[145, 16], [11, 16], [28, 14], [259, 16]]; // pass / block / warn / advisory
        for (var i = 0; i < zones.length; i++) {
            var d = Math.abs(hue - zones[i][0]);
            d = Math.min(d, 360 - d);
            if (d <= zones[i][1]) return "hex_err_semantic";
        }
        return null;
    }

    /* ---- state ---- */
    function genRequestId() {
        var hex = "";
        try {
            var buf = new Uint8Array(4);
            window.crypto.getRandomValues(buf);
            for (var i = 0; i < buf.length; i++) {
                hex += ("0" + buf[i].toString(16)).slice(-2);
            }
        } catch (e) {
            for (var j = 0; j < 8; j++) hex += Math.floor(Math.random() * 16).toString(16);
        }
        return "REQ-" + hex.toUpperCase();
    }

    var LANG = "zh";
    var STATE = {
        stage: 1,
        selections: {
            company: "", report_type: null, audience: null, audience_custom: "",
            purpose: "", brief_title: "",
            cadence: null, window: null, window_custom: "", language: null, source: null,
            formats: [], presentation: null, custom_base: "executive_brief",
            density: null, tables: null, citations: null,
            accent: "forest", accent_hex: "", accent_hex_raw: ""
        },
        freeText: "",
        interpretation: { mapped: [], unresolved: [] },
        dispositions: {}, // "field:value" -> "accepted" | "discarded"
        requestId: genRequestId(), // generated ONCE per page load; reused for resubmits
        workspaceTarget: "./market-weekly",
        submitting: false
    };

    function t(key) { return (MESSAGES[LANG] && MESSAGES[LANG][key]) || MESSAGES.zh[key] || key; }
    function optionText(option) { return LANG === "en" ? option.en : option.zh; }
    function optionLabel(list, id) {
        var found = null;
        (list || []).forEach(function (o) { if (o.id === id) found = o; });
        return found ? optionText(found)[0] : id;
    }
    function enLabel(list, id) {
        var found = null;
        (list || []).forEach(function (o) { if (o.id === id) found = o; });
        return found ? found.en[0] : String(id || "");
    }
    function el(tag, cls, text) {
        var node = document.createElement(tag);
        if (cls) node.className = cls;
        if (text != null) node.textContent = text;
        return node;
    }

    /* ---- choice-card component (enumField fork) ---- */
    function enumField(host, opts) {
        // opts: { num, titleKey, noteKey, list, get, set, recIds: {det:[], agent:[]},
        //         multi, customId, customPlaceholder, customGet, customSet, onChange }
        var sec = el("div", "section");
        var head = el("div", "section-head");
        head.appendChild(el("span", "section-num", opts.num));
        head.appendChild(el("span", "section-title", t(opts.titleKey)));
        sec.appendChild(head);
        if (opts.noteKey) sec.appendChild(el("p", "section-note", t(opts.noteKey)));
        var wrap = el("div", "chips");
        var customInput = null;

        function current() { return opts.get(); }
        function isSelected(id) {
            return opts.multi ? current().indexOf(id) >= 0 : current() === id;
        }
        function refresh() {
            wrap.querySelectorAll(".chip").forEach(function (chip) {
                chip.classList.toggle("selected", isSelected(chip.dataset.id));
            });
            if (customInput) customInput.classList.toggle("is-hidden", !isSelected(opts.customId));
        }
        opts.list.forEach(function (o) {
            var chip = el("button", "chip" + (opts.multi ? " multi" : ""));
            chip.type = "button";
            chip.dataset.id = o.id;
            var texts = optionText(o);
            chip.appendChild(el("span", "chip-label", texts[0]));
            if (texts[1]) chip.appendChild(el("span", "chip-desc", texts[1]));
            if (opts.recIds && opts.recIds.det.indexOf(o.id) >= 0) {
                chip.appendChild(el("span", "chip-rec det", t("recommended_det")));
            }
            if (opts.recIds && opts.recIds.agent.indexOf(o.id) >= 0) {
                chip.appendChild(el("span", "chip-rec agent", t("recommended_agent")));
            }
            chip.addEventListener("click", function () {
                if (opts.multi) {
                    var cur = current().slice();
                    var i = cur.indexOf(o.id);
                    if (i >= 0) cur.splice(i, 1); else cur.push(o.id);
                    opts.set(cur);
                } else {
                    opts.set(o.id);
                }
                refresh();
                if (opts.onChange) opts.onChange(o.id);
            });
            wrap.appendChild(chip);
        });
        sec.appendChild(wrap);
        if (opts.customId) {
            customInput = el("input", "chip-custom-input is-hidden");
            customInput.placeholder = t(opts.customPlaceholder);
            customInput.value = opts.customGet ? opts.customGet() : "";
            customInput.addEventListener("input", function () {
                if (opts.customSet) opts.customSet(customInput.value);
            });
            sec.appendChild(customInput);
        }
        host.appendChild(sec);
        refresh();
        return { refresh: refresh, node: sec };
    }

    /* plain text field section */
    function textField(host, opts) {
        // opts: { num, titleKey, noteKey, placeholderKey, textarea, get, set, onInput }
        var sec = el("div", "section");
        var head = el("div", "section-head");
        head.appendChild(el("span", "section-num", opts.num));
        head.appendChild(el("span", "section-title", t(opts.titleKey)));
        sec.appendChild(head);
        if (opts.noteKey) sec.appendChild(el("p", "section-note", t(opts.noteKey)));
        var input = el(opts.textarea ? "textarea" : "input", "text-input");
        if (opts.placeholderKey) input.placeholder = t(opts.placeholderKey);
        input.value = opts.get();
        input.addEventListener("input", function () {
            opts.set(input.value);
            if (opts.onInput) opts.onInput();
        });
        sec.appendChild(input);
        host.appendChild(sec);
        return { node: sec, input: input };
    }

    function agentRecIds(field) {
        return STATE.interpretation.mapped
            .filter(function (m) { return m.field === field; })
            .map(function (m) { return m.value; });
    }

    /* ---- stage renderers ---- */
    var sectionsHost = document.getElementById("sections");

    function clearHost() { sectionsHost.replaceChildren(); }

    function renderStage1() {
        clearHost();
        textField(sectionsHost, {
            num: "1.1", titleKey: "sec_company", placeholderKey: "placeholder_company",
            get: function () { return STATE.selections.company; },
            set: function (v) { STATE.selections.company = v; },
            onInput: updateActionbar
        });
        enumField(sectionsHost, {
            num: "1.2", titleKey: "sec_report_type", list: CATALOG.report_types,
            get: function () { return STATE.selections.report_type; },
            set: function (v) { STATE.selections.report_type = v; },
            onChange: renderStage1Keep
        });
        enumField(sectionsHost, {
            num: "1.3", titleKey: "sec_audience", list: CATALOG.audiences,
            get: function () { return STATE.selections.audience; },
            set: function (v) { STATE.selections.audience = v; },
            recIds: { det: [], agent: agentRecIds("audience") },
            customId: "custom_audience",
            customGet: function () { return STATE.selections.audience_custom; },
            customSet: function (v) { STATE.selections.audience_custom = v; }
        });
        textField(sectionsHost, {
            num: "1.4", titleKey: "sec_purpose", placeholderKey: "placeholder_purpose",
            get: function () { return STATE.selections.purpose; },
            set: function (v) { STATE.selections.purpose = v; },
            onInput: updateActionbar
        });
        textField(sectionsHost, {
            num: "1.5", titleKey: "sec_brief_title", placeholderKey: "placeholder_brief_title",
            get: function () { return STATE.selections.brief_title; },
            set: function (v) { STATE.selections.brief_title = v; }
        });
        enumField(sectionsHost, {
            num: "1.6", titleKey: "sec_cadence", list: CATALOG.cadences,
            get: function () { return STATE.selections.cadence; },
            set: function (v) { STATE.selections.cadence = v; },
            recIds: { det: [], agent: agentRecIds("cadence") }
        });
        enumField(sectionsHost, {
            num: "1.7", titleKey: "sec_window", list: CATALOG.windows,
            get: function () { return STATE.selections.window; },
            set: function (v) { STATE.selections.window = v; },
            customId: "custom_window", customPlaceholder: "placeholder_window",
            customGet: function () { return STATE.selections.window_custom; },
            customSet: function (v) { STATE.selections.window_custom = v; }
        });
        enumField(sectionsHost, {
            num: "1.8", titleKey: "sec_language", list: CATALOG.languages,
            get: function () { return STATE.selections.language; },
            set: function (v) { STATE.selections.language = v; }
        });
        enumField(sectionsHost, {
            num: "1.9", titleKey: "sec_source", list: CATALOG.sources,
            get: function () { return STATE.selections.source; },
            set: function (v) { STATE.selections.source = v; }
        });

        var ftField = textField(sectionsHost, {
            num: "1.10", titleKey: "sec_freetext", noteKey: "note_freetext",
            placeholderKey: "placeholder_freetext", textarea: true,
            get: function () { return STATE.freeText; },
            set: function (v) { STATE.freeText = v; },
            onInput: function () {
                STATE.interpretation = interpretFreeText(STATE.freeText);
                STATE.dispositions = {};
                paintInterp(ftField.node);
                updateActionbar();
            }
        });
        var interpSlot = el("div");
        interpSlot.id = "interp-slot";
        ftField.node.appendChild(interpSlot);
        paintInterp(ftField.node);
    }

    function renderStage1Keep() { /* keep scroll; light refresh of rec badges */
        renderStage1(); updateActionbar(); paintPreview();
    }

    function paintInterp(secF) {
        var slot = secF.querySelector("#interp-slot");
        if (!slot) return;
        slot.replaceChildren();
        var interp = STATE.interpretation;
        if (!interp.mapped.length && !interp.unresolved.length) return;
        var box = el("div", "interp-box");
        var head = el("div", "interp-head");
        head.textContent = "◆ " + t("interp_title");
        box.appendChild(head);
        interp.mapped.forEach(function (m) {
            var row = el("div", "interp-row");
            row.appendChild(el("span", "from", "“" + m.from + "”"));
            row.appendChild(el("span", "arrow", "→"));
            row.appendChild(el("span", "to", t(m.labelKey) + " = " + displayValue(m.field, m.value)));
            box.appendChild(row);
        });
        interp.unresolved.forEach(function (u) {
            var row = el("div", "interp-row unresolved");
            row.textContent = "“" + u + "” — " + t("interp_unresolved");
            box.appendChild(row);
        });
        box.appendChild(el("p", "interp-note", t("interp_note")));
        slot.appendChild(box);
    }

    function renderStage2() {
        clearHost();
        var rec = DET_REC[STATE.selections.report_type] || {};
        enumField(sectionsHost, {
            num: "2.1", titleKey: "sec_formats", list: CATALOG.formats, multi: true,
            get: function () { return STATE.selections.formats; },
            set: function (v) { STATE.selections.formats = v; },
            recIds: { det: rec.formats || [], agent: agentRecIds("formats") },
            onChange: function () { paintPreview(); updateActionbar(); }
        });
        enumField(sectionsHost, {
            num: "2.2", titleKey: "sec_presentation", list: CATALOG.presentations,
            get: function () { return STATE.selections.presentation; },
            set: function (v) { STATE.selections.presentation = v; },
            recIds: { det: rec.presentation ? [rec.presentation] : [], agent: [] },
            onChange: function () { renderStage2(); paintPreview(); updateActionbar(); }
        });
        if (STATE.selections.presentation === "custom_style") {
            enumField(sectionsHost, {
                num: "2.2.1", titleKey: "custom_base_title", noteKey: "custom_base_note",
                list: CATALOG.presentations.slice(0, 3),
                get: function () { return STATE.selections.custom_base; },
                set: function (v) { STATE.selections.custom_base = v; },
                onChange: function () { paintPreview(); }
            });
        }
        enumField(sectionsHost, {
            num: "2.3", titleKey: "sec_density", list: CATALOG.densities,
            get: function () { return STATE.selections.density; },
            set: function (v) { STATE.selections.density = v; },
            recIds: { det: rec.density ? [rec.density] : [], agent: agentRecIds("density") },
            onChange: function () { paintPreview(); updateActionbar(); }
        });
        enumField(sectionsHost, {
            num: "2.4", titleKey: "sec_tables", list: CATALOG.tables,
            get: function () { return STATE.selections.tables; },
            set: function (v) { STATE.selections.tables = v; },
            recIds: { det: [], agent: agentRecIds("tables") }
        });
        enumField(sectionsHost, {
            num: "2.5", titleKey: "sec_citations", list: CATALOG.citations,
            get: function () { return STATE.selections.citations; },
            set: function (v) { STATE.selections.citations = v; }
        });
        var accentField = enumField(sectionsHost, {
            num: "2.6", titleKey: "sec_accent", list: CATALOG.accents,
            get: function () { return STATE.selections.accent; },
            set: function (v) { STATE.selections.accent = v; },
            customId: "custom_hex", customPlaceholder: "placeholder_hex",
            customGet: function () { return STATE.selections.accent_hex_raw; },
            customSet: onHexInput,
            onChange: function () { paintPreview(); paintHexMsg(); }
        });
        var hexMsg = el("div", "hex-msg");
        hexMsg.id = "hex-msg";
        accentField.node.appendChild(hexMsg);
        paintHexMsg();
    }

    function onHexInput(v) {
        STATE.selections.accent_hex_raw = v;
        var hex = normalizeHex(v);
        if (!hex) {
            STATE.selections.accent_hex = "";
        } else if (accentVerdict(hex)) {
            STATE.selections.accent_hex = "";
        } else {
            STATE.selections.accent_hex = hex;
        }
        paintHexMsg();
        paintPreview();
    }

    function paintHexMsg() {
        var msg = document.getElementById("hex-msg");
        if (!msg) return;
        msg.className = "hex-msg";
        msg.textContent = "";
        if (STATE.selections.accent !== "custom_hex") return;
        var raw = STATE.selections.accent_hex_raw;
        if (!raw.trim()) return;
        var hex = normalizeHex(raw);
        if (!hex) {
            msg.textContent = t("hex_err_format");
            msg.classList.add("err");
            return;
        }
        var verdict = accentVerdict(hex);
        if (verdict) {
            msg.textContent = t(verdict);
            msg.classList.add("err");
            return;
        }
        msg.textContent = hex + " · " + t("hex_ok");
        msg.classList.add("ok");
    }

    function displayValue(field, value) {
        var raw = String(field).replace(/^field_/, "");
        var map = {
            report_type: CATALOG.report_types, audience: CATALOG.audiences,
            cadence: CATALOG.cadences, window: CATALOG.windows,
            language: CATALOG.languages, source: CATALOG.sources,
            presentation: CATALOG.presentations, density: CATALOG.densities,
            tables: CATALOG.tables, citations: CATALOG.citations, accent: CATALOG.accents
        };
        if (raw === "formats") return optionLabel(CATALOG.formats, value);
        if (raw === "purpose" || raw === "company" || raw === "brief_title") return value;
        if (raw === "window" && value === "custom_window") {
            return STATE.selections.window_custom || optionLabel(CATALOG.windows, value);
        }
        if (raw === "accent" && value === "custom_hex") {
            return STATE.selections.accent_hex || STATE.selections.accent_hex_raw || optionLabel(CATALOG.accents, value);
        }
        if (raw === "presentation" && value === "custom_style") {
            return optionLabel(CATALOG.presentations, value) +
                "（" + t("custom_base_title") + ": " + optionLabel(CATALOG.presentations, STATE.selections.custom_base) + "）";
        }
        return map[raw] ? optionLabel(map[raw], value) : value;
    }

    function confirmedSelections() {
        // explicit selections + accepted proposals
        var out = {};
        Object.keys(STATE.selections).forEach(function (k) {
            var v = STATE.selections[k];
            out[k] = Array.isArray(v) ? v.slice() : v;
        });
        STATE.interpretation.mapped.forEach(function (m) {
            if (STATE.dispositions[m.field + ":" + m.value] === "accepted") {
                if (m.field === "formats") {
                    if (out.formats.indexOf(m.value) < 0) out.formats.push(m.value);
                } else {
                    out[m.field] = m.value;
                }
            }
        });
        return out;
    }

    function reviewRows() {
        var rows = { explicit: [], proposed: [], unresolved: [] };
        var s = STATE.selections;
        function push(field, value) {
            if (value == null || value === "" || (Array.isArray(value) && !value.length)) return;
            var shown = Array.isArray(value)
                ? value.map(function (v) { return displayValue(field, v); }).join(" + ")
                : displayValue(field, value);
            rows.explicit.push([t(field), shown]);
        }
        push("field_company", s.company.trim());
        push("field_report_type", s.report_type);
        push("field_audience", s.audience === "custom_audience" ? (s.audience_custom || s.audience) : s.audience);
        push("field_purpose", s.purpose);
        push("field_brief_title", s.brief_title.trim());
        push("field_cadence", s.cadence);
        push("field_window", s.window);
        push("field_language", s.language);
        push("field_source", s.source);
        push("field_formats", s.formats);
        push("field_presentation", s.presentation);
        push("field_density", s.density);
        push("field_tables", s.tables);
        push("field_citations", s.citations);
        push("field_accent", s.accent);
        if (STATE.freeText.trim()) rows.explicit.push([t("field_freetext"), "“" + STATE.freeText.trim() + "”"]);
        STATE.interpretation.mapped.forEach(function (m) {
            rows.proposed.push(m);
        });
        STATE.interpretation.unresolved.forEach(function (u) { rows.unresolved.push(u); });
        return rows;
    }

    function renderStage3() {
        clearHost();
        var rows = reviewRows();

        var g1 = el("div", "review-group explicit");
        var h1 = el("div", "review-group-head");
        h1.appendChild(el("span", "dot"));
        h1.appendChild(el("span", null, t("review_explicit")));
        g1.appendChild(h1);
        var tb1 = el("table", "review-table");
        rows.explicit.forEach(function (r) {
            var tr = el("tr");
            tr.appendChild(el("th", null, r[0]));
            tr.appendChild(el("td", null, r[1]));
            tb1.appendChild(tr);
        });
        g1.appendChild(tb1);
        sectionsHost.appendChild(g1);

        var g2 = el("div", "review-group proposed");
        var h2 = el("div", "review-group-head");
        h2.appendChild(el("span", "dot"));
        h2.appendChild(el("span", null, t("review_proposed")));
        g2.appendChild(h2);
        if (!rows.proposed.length) {
            g2.appendChild(el("p", "section-note", t("review_none")));
        } else {
            var tb2 = el("table", "review-table");
            rows.proposed.forEach(function (m) {
                var key = m.field + ":" + m.value;
                var tr = el("tr");
                tr.appendChild(el("th", null, t(m.labelKey)));
                var td = el("td");
                td.appendChild(el("span", null, "“" + m.from + "” → " + displayValue(m.field, m.value) + " "));
                var d = STATE.dispositions[key];
                if (d === "accepted") {
                    td.appendChild(el("span", "accepted-tag", "✓ " + t("review_accepted")));
                } else if (d === "discarded") {
                    td.appendChild(el("span", "accepted-tag", "✗ " + t("review_discarded")));
                } else {
                    var acc = el("button", "accept", t("review_accept"));
                    acc.addEventListener("click", function () { STATE.dispositions[key] = "accepted"; renderStage3(); updateActionbar(); });
                    var dis = el("button", "accept", t("review_discard"));
                    dis.addEventListener("click", function () { STATE.dispositions[key] = "discarded"; renderStage3(); updateActionbar(); });
                    td.appendChild(acc);
                    td.appendChild(dis);
                }
                tr.appendChild(td);
                tb2.appendChild(tr);
            });
            g2.appendChild(tb2);
        }
        sectionsHost.appendChild(g2);

        if (rows.unresolved.length) {
            var g3 = el("div", "review-group unresolved");
            var h3 = el("div", "review-group-head");
            h3.appendChild(el("span", "dot"));
            h3.appendChild(el("span", null, t("review_unresolved")));
            g3.appendChild(h3);
            var tb3 = el("table", "review-table");
            rows.unresolved.forEach(function (u) {
                var tr = el("tr");
                tr.appendChild(el("th", null, "“…”"));
                tr.appendChild(el("td", null, "“" + u + "”"));
                tb3.appendChild(tr);
            });
            g3.appendChild(tb3);
            sectionsHost.appendChild(g3);
        }

        var path = el("div", "review-path");
        path.appendChild(el("span", "k", t("review_path_k")));
        var targetInput = el("input", "ws-target-input");
        targetInput.value = STATE.workspaceTarget;
        targetInput.addEventListener("input", function () {
            STATE.workspaceTarget = targetInput.value;
        });
        path.appendChild(targetInput);
        sectionsHost.appendChild(path);

        sectionsHost.appendChild(el("p", "review-warning", t("review_statement")));
    }

    /* ---- synthetic preview (fixed public-safe sample) ---- */
    var SAMPLE = {
        zh: {
            kicker: "行业周报 · 合成样例",
            title: "组件现货价格连续第三周回落",
            summary: "监测样本显示，组件现货均价本周环比下降 1.5%，连续第三周回落。采购窗口可能出现，但需求侧信号仍不明确。",
            section: "关键信号",
            bullets: ["组件均价环比 -1.5%，连续第三周回落", "两家头部厂商下调下季度排产预期", "上游硅料价格持平，成本传导有限"],
            th: ["指标", "本周", "环比"],
            rows: [["组件均价（样本）", "0.83 元/W", "-1.5%"], ["硅料均价", "62 元/kg", "0.0%"], ["排产预期指数", "47.2", "-3.1"]],
            risk: "风险观察：若下游电站招标继续推迟，价格回落可能延续；当前材料不足以判断需求是否回暖。",
            src: "来源：SRC-009（2026-06-05）· SRC-014（2026-06-07）· 样本口径见来源附录"
        },
        en: {
            kicker: "Industry weekly · synthetic sample",
            title: "Module spot prices fall for a third week",
            summary: "The monitored sample shows module average spot prices down 1.5% week over week, a third consecutive decline. A procurement window may open, but demand signals remain unclear.",
            section: "Key signals",
            bullets: ["Module average price -1.5% WoW, third weekly decline", "Two leading producers cut next-quarter output guidance", "Upstream polysilicon flat; limited cost pass-through"],
            th: ["Metric", "This week", "WoW"],
            rows: [["Module avg (sample)", "¥0.83/W", "-1.5%"], ["Polysilicon avg", "¥62/kg", "0.0%"], ["Output guidance index", "47.2", "-3.1"]],
            risk: "Watch: if downstream tenders keep slipping, declines may continue; current material is insufficient to judge a demand recovery.",
            src: "Sources: SRC-009 (2026-06-05) · SRC-014 (2026-06-07) · sample scope in the source appendix"
        }
    };

    function resolvedPresentation() {
        var pres = STATE.selections.presentation || "executive_brief";
        if (pres === "custom_style") pres = STATE.selections.custom_base || "executive_brief";
        return pres;
    }

    /* custom accent via CSSOM insertRule on the same-origin stylesheet
       (CSP style-src 'self' forbids inline style attributes). */
    var accentRuleIndex = -1;
    function ownStyleSheet() {
        for (var i = 0; i < document.styleSheets.length; i++) {
            try {
                if (document.styleSheets[i].cssRules) return document.styleSheets[i];
            } catch (e) { /* inaccessible sheet */ }
        }
        return null;
    }
    function dropAccentRule() {
        if (accentRuleIndex < 0) return;
        var sheet = ownStyleSheet();
        if (sheet) {
            try { sheet.deleteRule(accentRuleIndex); } catch (e) { /* ignore */ }
        }
        accentRuleIndex = -1;
    }
    function paintAccent() {
        var panel = document.getElementById("preview-panel");
        var accentId = STATE.selections.accent || "forest";
        if (accentId === "custom_hex" && STATE.selections.accent_hex) {
            panel.setAttribute("data-accent", "custom");
            dropAccentRule();
            var sheet = ownStyleSheet();
            if (sheet) {
                try {
                    accentRuleIndex = sheet.cssRules.length;
                    sheet.insertRule(
                        '#preview-panel[data-accent="custom"] { --accent: ' + STATE.selections.accent_hex + "; }",
                        accentRuleIndex
                    );
                } catch (e) { accentRuleIndex = -1; }
            }
        } else {
            dropAccentRule();
            panel.setAttribute("data-accent", accentId === "custom_hex" ? "forest" : accentId);
        }
    }

    function paintPreview() {
        var host = document.getElementById("topbar-preview");
        host.replaceChildren();
        var s = SAMPLE[LANG];
        paintAccent();

        var frame = el("div", "preview-frame");
        var fh = el("div", "preview-frame-head");
        var fmts = STATE.selections.formats.length
            ? STATE.selections.formats.map(function (f) { return displayValue("formats", f); }).join(" + ")
            : "—";
        fh.appendChild(el("span", null, t("preview_formats") + fmts));
        var fidNotes = [];
        if (STATE.selections.formats.indexOf("html") >= 0) fidNotes.push(t("preview_fidelity_html"));
        if (STATE.selections.formats.indexOf("docx") >= 0) fidNotes.push(t("preview_fidelity_docx"));
        fh.appendChild(el("span", "fidelity", fidNotes.join(" · ")));
        frame.appendChild(fh);

        var pres = resolvedPresentation();
        var dens = STATE.selections.density || "balanced";
        var doc = el("div", "preview-doc p-" + pres + " d-" + dens);
        doc.appendChild(el("div", "doc-kicker", s.kicker));
        doc.appendChild(el("h2", "doc-title", s.title));
        doc.appendChild(el("p", "doc-summary", s.summary));
        doc.appendChild(el("div", "doc-section", s.section));
        var ul = el("ul");
        s.bullets.forEach(function (b) { ul.appendChild(el("li", null, b)); });
        doc.appendChild(ul);
        if ((STATE.selections.tables || "preserve_full") !== "prose") {
            var table = el("table");
            var trh = el("tr");
            s.th.forEach(function (h) { trh.appendChild(el("th", null, h)); });
            table.appendChild(trh);
            var rows = STATE.selections.tables === "key_only" ? s.rows.slice(0, 1) : s.rows;
            rows.forEach(function (r) {
                var tr = el("tr");
                r.forEach(function (c) { tr.appendChild(el("td", null, c)); });
                table.appendChild(tr);
            });
            doc.appendChild(table);
        }
        doc.appendChild(el("div", "doc-risk", s.risk));
        doc.appendChild(el("div", "doc-src", s.src));
        frame.appendChild(doc);
        host.appendChild(frame);
    }

    /* ---- validation ---- */
    function missingRequired() {
        var s = STATE.selections, miss = [];
        if (!s.company.trim()) miss.push(t("field_company"));
        if (!s.report_type) miss.push(t("field_report_type"));
        if (!s.audience) miss.push(t("field_audience"));
        if (s.audience === "custom_audience" && !s.audience_custom.trim()) miss.push(t("field_audience"));
        if (!s.purpose.trim() && !STATE.freeText.trim()) miss.push(t("field_purpose"));
        if (!s.cadence) miss.push(t("field_cadence"));
        if (!s.language) miss.push(t("field_language"));
        if (!s.source) miss.push(t("field_source"));
        if (!s.formats.length) miss.push(t("field_formats"));
        if (!s.presentation) miss.push(t("field_presentation"));
        if (!s.density) miss.push(t("field_density"));
        return miss;
    }

    function pendingProposals() {
        return STATE.interpretation.mapped.filter(function (m) {
            return !STATE.dispositions[m.field + ":" + m.value];
        });
    }

    /* ---- actionbar / navigation ---- */
    var btnBack = document.getElementById("btn-back");
    var btnNext = document.getElementById("btn-next");
    var btnConfirm = document.getElementById("btn-confirm");
    var confirmStatus = document.getElementById("confirm-status");

    function updateActionbar() {
        btnBack.disabled = STATE.stage === 1;
        btnNext.hidden = STATE.stage === 3;
        btnConfirm.hidden = STATE.stage !== 3;
        confirmStatus.classList.remove("err");
        if (!SESSION.token || !SESSION.sessionId) {
            btnConfirm.disabled = true;
            confirmStatus.textContent = t("err_session");
            confirmStatus.classList.add("err");
            return;
        }
        if (STATE.stage === 3) {
            var miss = missingRequired();
            var pend = pendingProposals();
            if (miss.length) {
                btnConfirm.disabled = true;
                confirmStatus.textContent = t("err_required") + miss.join("、");
                confirmStatus.classList.add("err");
            } else if (pend.length) {
                btnConfirm.disabled = true;
                confirmStatus.textContent = t("err_pending");
                confirmStatus.classList.add("err");
            } else {
                btnConfirm.disabled = false;
                confirmStatus.textContent = t("status_ready");
            }
        } else {
            confirmStatus.textContent = "";
        }
    }

    function paintSteps() {
        var steps = document.getElementById("stage-steps");
        steps.replaceChildren();
        [1, 2, 3].forEach(function (n) {
            var pill = el("div", "step-pill" + (STATE.stage === n ? " active" : STATE.stage > n ? " done" : ""));
            pill.appendChild(el("span", "step-n", STATE.stage > n ? "✓" : String(n)));
            pill.appendChild(el("span", null, t("step_" + n)));
            steps.appendChild(pill);
        });
    }

    function goStage(n) {
        STATE.stage = n;
        paintSteps();
        if (n === 1) renderStage1();
        if (n === 2) renderStage2();
        if (n === 3) renderStage3();
        updateActionbar();
        document.getElementById("form").scrollTop = 0;
    }

    btnBack.addEventListener("click", function () { if (STATE.stage > 1) goStage(STATE.stage - 1); });
    btnNext.addEventListener("click", function () { if (STATE.stage < 3) goStage(STATE.stage + 1); });

    /* ---- submission → real loopback endpoint → receipt/error overlay ---- */
    var overlay = document.getElementById("confirmed-overlay");
    var cfBody = document.getElementById("cf-body");

    function buildSubmission() {
        var c = confirmedSelections();
        var reportLabel = enLabel(CATALOG.report_types, c.report_type);
        var audience = c.audience === "custom_audience"
            ? String(c.audience_custom || "").trim()
            : enLabel(CATALOG.audiences, c.audience);
        var objective = String(c.purpose || "").trim() || STATE.freeText.trim();
        var cadence = c.cadence === "one_time" ? "ad_hoc" : String(c.cadence || "weekly");
        if (["weekly", "biweekly", "monthly", "ad_hoc"].indexOf(cadence) < 0) cadence = "weekly";
        return {
            schema_version: "briefloop.init_web.submission.v1",
            request_id: STATE.requestId,
            payload: {
                workspace_target: STATE.workspaceTarget,
                selections: {
                    company: String(c.company || "").trim(),
                    industry_or_theme: reportLabel,
                    task_objective: objective,
                    brief_title: String(c.brief_title || "").trim(),
                    audience: audience,
                    interface_language: LANG,
                    output_language: c.language === "en" ? "en" : "zh",
                    cadence: cadence,
                    focus_areas: [reportLabel],
                    output_formats: (c.formats || []).slice(),
                    forbidden_sources: c.source === "local_only" ? ["public_web"] : [],
                    web_search_mode: "disabled"
                },
                raw_free_text: STATE.freeText.trim(),
                discarded: STATE.interpretation.mapped.filter(function (m) {
                    return STATE.dispositions[m.field + ":" + m.value] === "discarded";
                }).map(function (m) { return m.field + "=" + m.value; }),
                human_confirmation: true // set only here, from the explicit confirm button
            }
        };
    }

    function submitRequest() {
        if (STATE.submitting) return;
        STATE.submitting = true;
        overlay.hidden = false;
        paintSubmitting();
        fetch("/api/v1/submit?session_id=" + encodeURIComponent(SESSION.sessionId), {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-BriefLoop-Session-Token": SESSION.token
            },
            body: JSON.stringify(buildSubmission())
        }).then(function (res) {
            return res.json().then(function (body) {
                STATE.submitting = false;
                handleResponse(res.status, body);
            }, function () {
                STATE.submitting = false;
                paintError("init_web_response_invalid");
            });
        }).catch(function () {
            STATE.submitting = false;
            paintError("init_web_network_error");
        });
    }

    function handleResponse(httpStatus, body) {
        if (httpStatus === 200 && body && body.ok === true) {
            paintReceipt(body);
            return;
        }
        var reason = body && body.reason_code ? String(body.reason_code) : "unknown_error";
        paintError(reason);
    }

    function paintSubmitting() {
        cfBody.replaceChildren();
        var status = el("div", "cf-status");
        status.id = "cf-title";
        status.appendChild(el("span", null, t("cf_submitting")));
        cfBody.appendChild(status);
    }

    function paintReceipt(response) {
        cfBody.replaceChildren();
        var replayed = response.status === "replayed";
        var status = el("div", "cf-status");
        status.id = "cf-title";
        status.appendChild(el("span", "cf-icon", "✓"));
        status.appendChild(el("span", null, t(replayed ? "cf_replayed" : "cf_committed")));
        if (replayed) status.appendChild(el("span", "cf-badge-replay", t("cf_replayed_badge")));
        cfBody.appendChild(status);
        cfBody.appendChild(el("p", "cf-sub", t(replayed ? "cf_sub_replayed" : "cf_sub_committed")));

        var box = el("div", "cf-receipt");
        [["workspace_id", response.workspace_id],
         ["run_id", response.run_id],
         ["transaction_id", response.transaction_id],
         ["committed_revision", response.committed_revision],
         ["workspace", response.workspace]].forEach(function (kv) {
            var line = el("div");
            line.appendChild(el("span", "k", kv[0] + "  "));
            line.appendChild(el("span", null, String(kv[1])));
            box.appendChild(line);
        });
        cfBody.appendChild(box);

        var next = el("p", "cf-next");
        next.appendChild(el("span", null, t("cf_next")));
        next.appendChild(el("code", null,
            "briefloop run --workspace " + String(response.workspace || STATE.workspaceTarget)));
        cfBody.appendChild(next);

        var actions = el("div", "cf-actions");
        var again = el("button", "btn-ghost", t("cf_again"));
        again.addEventListener("click", submitRequest);
        actions.appendChild(again);
        var close = el("button", "btn-ghost", t("cf_close"));
        close.addEventListener("click", function () { overlay.hidden = true; });
        actions.appendChild(close);
        cfBody.appendChild(actions);
        cfBody.appendChild(el("p", "cf-note", t("cf_note")));
    }

    function paintError(reasonCode) {
        cfBody.replaceChildren();
        var conflict = reasonCode === "submission_replay_conflict";
        var status = el("div", "cf-status cf-conflict");
        status.id = "cf-title";
        status.appendChild(el("span", "cf-icon cf-icon-block", "✗"));
        status.appendChild(el("span", null, t(conflict ? "cf_conflict" : "cf_error")));
        cfBody.appendChild(status);
        cfBody.appendChild(el("div", "cf-reason", reasonCode));
        cfBody.appendChild(el("p", "cf-sub", t(conflict ? "cf_sub_conflict" : "cf_sub_error")));
        var actions = el("div", "cf-actions");
        var close = el("button", "btn-primary", t("cf_close"));
        close.addEventListener("click", function () { overlay.hidden = true; });
        actions.appendChild(close);
        cfBody.appendChild(actions);
    }

    btnConfirm.addEventListener("click", function () {
        if (!SESSION.token || !SESSION.sessionId) {
            overlay.hidden = false;
            paintError("init_web_session_missing");
            return;
        }
        submitRequest();
    });

    /* ---- language toggle ---- */
    var langBtn = document.getElementById("btn-lang-toggle");
    var langMenu = document.getElementById("lang-menu");
    langBtn.addEventListener("click", function () {
        var open = !langMenu.hidden;
        langMenu.hidden = open;
        langBtn.setAttribute("aria-expanded", open ? "false" : "true");
    });
    langMenu.querySelectorAll("li").forEach(function (li) {
        li.addEventListener("click", function () {
            LANG = li.dataset.lang;
            document.getElementById("lang-current").textContent = li.textContent;
            document.documentElement.lang = LANG === "en" ? "en" : "zh-CN";
            langMenu.querySelectorAll("li").forEach(function (x) {
                x.setAttribute("aria-selected", x === li ? "true" : "false");
            });
            langMenu.hidden = true;
            langBtn.setAttribute("aria-expanded", "false");
            applyStaticTranslations();
            paintSteps();
            goStage(STATE.stage);
            paintPreview();
        });
    });
    function applyStaticTranslations() {
        document.querySelectorAll("[data-i18n]").forEach(function (node) {
            node.textContent = t(node.dataset.i18n);
        });
    }

    /* ---- boot ---- */
    applyStaticTranslations();
    paintSteps();
    renderStage1();
    paintPreview();
    updateActionbar();
})();
