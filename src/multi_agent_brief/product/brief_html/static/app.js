/* ==========================================================================
   BriefLoop brief_html — local reader and audit pages (production static asset)
   Derived (MIT) from the BriefLoop quality-panel redesign prototype.
   Reads the embedded brief_pages.data.v2 payload and renders:
     tab 1 brief    — exact Store-bound local reader Markdown
     tab 2 quality  — deterministic Store projection (green = pass only)
     tab 3 review   — LAJ semantic advisory view (purple; never PASS wording)
     tab 4 feedback — Store-native Human guidance state (explicit successor opt-in)
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
            tab_review: "AI 语义复盘",
            tab_feedback: "反馈与改进",
            eyebrow: "审计附件 · AUDIT ATTACHMENT",
            panel_title: "质量面板",
            overall_status: "投影状态",
            meta_run: "运行",
            meta_generated: "生成时间",
            meta_revision: "Store revision",
            meta_authority: "权威来源",
            sec_control: "控制面完整性",
            sec_source: "来源与证据",
            sec_gates: "门禁结果",
            sec_claims: "主张支持与风险",
            sec_reader: "读者清洁与引用卫生",
            sec_closeout: "收口与交付包分离",
            sec_actions: "推荐的下一步动作",
            sec_projection: "Store 质量投影原文（verbatim JSON）",
            reason_code: "原因码",
            unavailable: "不可用",
            actions_none: "（无推荐动作）",
            laj_title: "AI 语义复盘（实验）",
            laj_sub: "以下为冻结仪器对当前终稿的语义层建议，不构成质量分数或交付裁决。",
            laj_not_run: "LAJ 未运行",
            laj_not_run_note: "本工作区没有可绑定的 LAJ reader 视图；此处不臆造任何评估结果。",
            laj_status: "视图状态",
            cov_assessed: "已评估单元",
            cov_findings: "findings",
            cov_withheld: "被扣留 findings",
            cov_abstentions: "弃权",
            dim_title: "九个维度概览（按评估单元状态，无分数）",
            dim_finding_reported: "报告了 finding",
            dim_not_assessed: "本视图未评估",
            findings_none: "本视图没有 finding。",
            f_unit: "评估单元",
            f_observation: "观察",
            f_rationale: "理由",
            f_severity_basis: "严重度依据",
            f_confidence_basis: "置信依据",
            f_action: "建议人工动作",
            f_external_premise: "外部前提披露",
            f_context_reqs: "上下文需求",
            f_rewrite: "建议改写",
            f_spans: "报告定位 spans",
            handoff_title: "交接说明（handoff 是证据需求，不是缺陷；从不触发 Gates）",
            reason_codes_title: "reason_codes",
            disclaimer_title: "免责声明",
            fb_title: "反馈与下一轮改进",
            fb_sub: "当前报告或所选 assessment 暂无可展示的 Store-qualified 人类审阅或已批准 guidance；本页不会臆造记录。",
            fb_available_sub: "以下为 Store 原生、人工编辑并单独批准的 guidance；仅在人类显式选择复用并启动后继 run 时消费。",
            recorded_title: "已记录的反馈",
            recorded_none: "（暂无记录）",
            il_unavailable: "当前报告或所选 assessment 暂无 Store-qualified 人类审阅或已批准 guidance。",
            disposition_title: "人工处置",
            disposition_accept: "接受",
            disposition_reject: "拒绝",
            disposition_defer: "暂缓",
            guidance_edit: "编辑 guidance",
            guidance_save: "保存草稿",
            guidance_approve: "单独批准",
            guidance_deactivate: "停用",
            guidance_revert: "撤回",
            guidance_supersede: "标记被替代",
            command_pending: "正在记录…",
            command_failed: "记录失败",
            command_saved: "已由 Store Receipt 记录",
            consumption_label: "下一轮消费边界 · ",
            planned_label: "planned",
            footer_boundary: "静态导出边界：本页永远是只读投影；不含任何命令端点或写入能力。",
            data_error: "嵌入数据缺失或无法解析；无法渲染。",
            tab_aria: "Brief pages sections",
            brief_title: "本地终稿",
            brief_unavailable: "终稿尚不可用",
            brief_local_boundary: "本页仅表示本地完成，不表示审批、打包、交付或发布。",
            brief_progress: "运行进度",
            brief_identity: "Store artifact"
        },
        en: {
            top_badge: "Read-only static export · no write affordance",
            session_badge: "Local Review Session · Human actions write SQLite",
            tab_brief: "Brief",
            tab_quality: "Quality status",
            tab_review: "AI semantic review",
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
            laj_title: "AI semantic review (experimental)",
            laj_sub: "Semantic-layer suggestions from the frozen instrument on the current reader. Not a quality score, not a delivery verdict.",
            laj_not_run: "LAJ not run",
            laj_not_run_note: "No bindable LAJ reader view exists for this workspace; nothing is fabricated here.",
            laj_status: "View status",
            cov_assessed: "assessed units",
            cov_findings: "findings",
            cov_withheld: "withheld findings",
            cov_abstentions: "abstentions",
            dim_title: "Nine dimensions by unit status (no scores)",
            dim_finding_reported: "finding reported",
            dim_not_assessed: "not assessed in view",
            findings_none: "No findings in this view.",
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
            command_pending: "Recording…",
            command_failed: "Command failed",
            command_saved: "Recorded by Store Receipt",
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
    var LANG = "zh";
    var STATE = { tab: ACTION_SESSION ? "review" : "brief" };

    function t(key) { return (MESSAGES[LANG] && MESSAGES[LANG][key]) || MESSAGES.zh[key] || key; }
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

    function applyReviewStatus(status) {
        if (!status || status.ok !== true) return;
        var byFinding = {};
        (status.dispositions || []).forEach(function (row) {
            byFinding[row.finding_id] = row;
        });
        ((DATA.semantic || {}).findings || []).forEach(function (finding) {
            var row = byFinding[finding.finding_id];
            if (row) {
                finding.finding_fingerprint = row.finding_fingerprint;
                finding.human_disposition = row.current;
            }
        });
        DATA.improvement.status = "available";
        DATA.improvement.recorded = status.guidance_drafts || [];
        DATA.improvement.guidance_statuses = status.guidance_statuses || [];
    }

    function sendReviewCommand(action, payload, statusNode) {
        if (!ACTION_SESSION) return;
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
            if (!response.ok) throw new Error("command rejected");
            return response.json();
        }).then(function (result) {
            applyReviewStatus(result.review_status);
            renderAll();
        }).catch(function () {
            statusNode.textContent = t("command_failed");
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
            String(brief.boundary || t("brief_local_boundary"))));
        var state = el("span", "status-pill-mini " +
            (brief.status === "available" ? "level-pass" : "level-missing"),
            String(brief.view_state || run.view_state || "setup"));
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
        left.appendChild(el("p", "hero-boundary", String(DATA.boundary || "")));
        hero.appendChild(left);

        var available = q.status === "available";
        var pill = el("div", "status-pill " + (available ? "level-pass" : "level-missing"));
        pill.appendChild(el("span", "sp-k", t("overall_status")));
        pill.appendChild(el("span", "sp-v", String(q.status || t("unavailable"))));
        if (!available && q.reason_code) {
            pill.appendChild(el("span", "sp-k", t("reason_code") + ": " + q.reason_code));
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
        strip.appendChild(el("span", "status-pill-mini " + (available ? "level-pass" : "level-missing"), String(q.status || t("unavailable"))));
        strip.appendChild(el("span", "identity-meta",
            String(ws.run_id || "") + " · rev " + String(ws.store_revision || "")));
        main.appendChild(strip);
    }

    function renderReview(main) {
        var sem = DATA.semantic || {};
        renderIdentityCompact(main);

        var zone = el("section", "advisory-zone");
        var banner = el("div", "advisory-banner");
        banner.appendChild(el("span", "ab-tag", "Advisory"));
        banner.appendChild(el("span", null, String(sem.banner || "")));
        zone.appendChild(banner);

        var body = el("div", "advisory-body");
        body.appendChild(el("h2", null, t("laj_title")));
        body.appendChild(el("p", "advisory-sub", t("laj_sub") + " " + String(sem.boundary || "")));

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
        statusRow.appendChild(el("span", "badge badge-advisory", String(sem.status || t("unavailable"))));
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

        body.appendChild(el("h2", null, t("dim_title")));
        var dims = el("div", "dim-strip");
        (sem.dimensions || []).forEach(function (d) {
            var chip = el("div", "dim-chip");
            chip.appendChild(el("span", "dim-name", String(d.dimension_id)));
            var reported = d.state === "finding_reported";
            chip.appendChild(el("span",
                "dim-status " + (reported ? "finding_reported" : "not_assessed_in_view"),
                t(reported ? "dim_finding_reported" : "dim_not_assessed")));
            dims.appendChild(chip);
        });
        body.appendChild(dims);

        var findings = sem.findings || [];
        if (!findings.length) {
            body.appendChild(el("p", "section-muted", t("findings_none")));
        }
        findings.forEach(function (f) { body.appendChild(findingCard(f)); });

        var ho = el("p", "handoff-note");
        ho.appendChild(el("strong", null, t("handoff_title") + " · "));
        ho.appendChild(el("span", null, String(sem.handoff_note || "")));
        body.appendChild(ho);

        var rcs = sem.reason_codes || [];
        if (rcs.length) {
            var rcRow = el("p", "laj-status-row");
            rcRow.appendChild(el("span", "fb-k", t("reason_codes_title") + "  "));
            rcs.forEach(function (rc) { rcRow.appendChild(el("span", "badge badge-missing", String(rc))); });
            body.appendChild(rcRow);
        }

        if (sem.disclaimer) {
            var dis = el("p", "cov-note");
            dis.appendChild(el("strong", null, t("disclaimer_title") + " · "));
            dis.appendChild(el("span", null, String(sem.disclaimer)));
            body.appendChild(dis);
        }

        zone.appendChild(body);
        main.appendChild(zone);
    }

    function findingCard(f) {
        var card = el("article", "finding-card");
        var head = el("div", "finding-head");
        head.appendChild(el("span", "f-dim", String(f.dimension_id || "")));
        head.appendChild(el("span", "badge badge-advisory", String(f.severity || "")));
        head.appendChild(el("span", "badge badge-info", String(f.impact_scope || "")));
        head.appendChild(el("span", "badge badge-info", String(f.scope_class || "")));
        if (f.status) head.appendChild(el("span", "badge badge-advisory", String(f.status)));
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
            row.appendChild(el("span", null, String(kv[1])));
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
                    String(f.human_disposition.decision) + " · " +
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
                        assessment_result_id: DATA.semantic.assessment_result_id,
                        finding_id: f.finding_id,
                        disposition_id: f.human_disposition.disposition_id,
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

    /* ---- feedback tab (inert; honest unavailable surface) ---- */
    function renderFeedback(main) {
        var imp = DATA.improvement || {};
        renderIdentityCompact(main);

        var zone = el("section", "feedback-zone");
        zone.appendChild(el("h2", null, t("fb_title")));
        zone.appendChild(el("p", "feedback-sub",
            t(imp.status === "available" ? "fb_available_sub" : "fb_sub")));

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
            card.appendChild(el("span", "badge badge-missing", String(imp.status || t("unavailable"))));
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
            document.querySelectorAll("[data-i18n]").forEach(function (node) {
                var key = node.dataset.i18n;
                node.textContent = t(
                    ACTION_SESSION && key === "top_badge" ? "session_badge" : key
                );
            });
            renderAll();
        });
    });

    /* ---- boot ---- */
    var initialHash = "";
    if (!ACTION_SESSION) {
        try { initialHash = location.hash.replace("#", ""); } catch (e) { /* ignore */ }
        if (TABS.some(function (tb) { return tb[0] === initialHash; })) STATE.tab = initialHash;
    } else {
        var topBadge = document.querySelector("[data-i18n='top_badge']");
        if (topBadge) topBadge.textContent = t("session_badge");
    }
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
