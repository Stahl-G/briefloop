"""Report-specific requirements; the shared reader contract controls presentation.

Only the industry increment lives here. Body/record boundaries, accuracy, reader
applicability and evaluation framing come from deliverable_spec, so the two rule sets
cannot drift or repeat each other in the same assembled prompt.
"""
INDUSTRY_SECTIONS = ['核心摘要', '价格成本与供应链', '需求规模与竞争', '政策融资与重点专题', '相关宏观金融', '对目标组织的启示']


def profile_context(requirements):
    if requirements.get('report_profile') != 'industry_periodic':
        return {}
    organization = requirements.get('organization') or '目标读者'
    return {'id': 'industry_periodic', 'sections': INDUSTRY_SECTIONS, 'organization': organization,
            'industry': requirements.get('industry', ''),
            'instructions': f'''行业报告内容增量：用可比数据、本期重点事件和分析回答对{organization}的经营影响。以六块作为通用起点：核心摘要、价格成本与供应链、需求规模与竞争、政策融资与重点专题、相关宏观金融、对目标组织的启示。用户已选模板章节或明确规定结构时遵守该结构；否则按 industry 与 objective 选择相关板块，不相关专题可省略。篇幅与图表数量服从本轮目标及内容需要，不按新闻逐篇复述。
行业方法口径：价格比较保留产品规格、地区、币种、税口径、交付期；政策保留提案/生效状态和实际适用范围；规模指标保留实际/预测/公司指引/一致预期类别；金融指标正确区分百分比与基点；融资、开工、计划产能、订单、投产与交付按来源所指阶段准确表述。报告发布日期 report_date、事件覆盖期 period 和指标截至日分别保存。
行业数据工具：数值取到后使用 prepare-report-data 工具，按登记来源和具体原文保存原始值并确定计算。report_data 只保存原始 records，derived 由工具生成。每条 record 必须包含：metric、unit、current、current_date（该指标对应的本期日期或期间日期）、source_id；预测/公司指引/一致预期还要 category 与 as_of（取得该数据的截至日）。要计算变化时另给 previous、previous_date、previous_unit、previous_tax_basis、previous_category，并把 comparable 设为 true；无法对应就写 category 与 notes 说明，不要省略这些字段让程序去猜。缺 current_date 或口径不一致的记录会被 prepare-report-data 记为数据缺口，不会替你补造。表格使用工具结果，解释与数值、单位、期间一致。同指标同单位的不同日期数据可用于趋势图，图表须支持具体论点；缺少投行、渠道或专有数据时不声称已完成同口径替代。
行业参考与补查：reference_sources 仅为风格/结构参考，旧报告事实不能自动作为本期依据。Analyst 将会改变核心结论的具体缺口写入内部研究结果（缺什么、影响、需要何种原始来源）；Orchestrator 在剩余共享预算内最多安排一次定向补查，复用已有原文。人工填写章节按指定占位检查。''',
            'evaluation': '''行业事实增量核查：对照 report_data 原始值、实际计算输出和 source_id/locator 的原文，核对表文数值、单位、量级、日期、事实/预测类别与主体适用条件；计算正确不证明输入或结论正确。不要求凑表、补造数字。发现以 report_quote、来源定位和原文依据说明；表达与分析使用同一份本轮读者约定（见共用读者规则）。'''}
