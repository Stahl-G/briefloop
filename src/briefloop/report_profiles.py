"""Report-specific deliverable requirements, independent of models and search providers."""
INDUSTRY_SECTIONS = ['核心摘要','价格成本与供应链','需求规模与竞争','政策融资与重点专题','相关宏观金融','对目标组织的启示']

def profile_context(requirements):
    if requirements.get('report_profile')!='industry_periodic':return {}
    organization=requirements.get('organization') or '目标读者'
    return {'id':'industry_periodic','sections':INDUSTRY_SECTIONS,'organization':organization,'industry':requirements.get('industry',''),
      'instructions':f'''产物是指定行业定期研究报告：固定数据面板、本期重点专题及对{organization}的经营含义。以以下六块为起点，按 industry 与 objective 裁剪、改名；不适用的专题可省略，避免机械套用公司或金融框架：核心摘要（4–5项，约550–750计数单位）、价格成本与供应链、需求规模与竞争、政策融资与重点专题、相关宏观金融（不相关时省略并说明）、对{organization}的风险/机会/经营/资本启示（约1100–1400单位；总篇幅优先服从用户要求）。不按新闻逐篇复述。
报告发布日期 report_date 与事件覆盖期 period、各指标数据截至日分开。日期晚于已获得数据时明确最近可得日期，不假造未来更新。价格比较保留产品规格、地区、币种、税口径、交付期；政策区分提案、生效和适用范围；规模指标区分实际、预测、公司指引和一致预期；金融指标区分百分比与基点。不能把融资/开工/计划产能写成订单/投产/实际交付。
取到数值后使用 prepare-report-data 工具，以登记来源和原文定位保存原始值并确定计算。report_data 只保存原始 records，不自行填 derived 值；表格使用工具 markdown，解释必须与数值/单位/期间一致。可从同指标同单位不同日期的已存数据绘制趋势；没有图表数据不凑图。投行/渠道/专有数据拿不到时列缺口，不能声称完成同口径替代。
reference_sources 仅为风格/结构参考：不得把旧报告事实自动当本期来源、把示例公司的事实套到当前公司，或声称当前报告沿用了未使用的投行数据。对公司的分析需事实→传导机制→条件/时间→经营含义→后续观察，有独立证据才提出建议。
允许 Analyst 在初次证据合并后提出会改变核心结论的具体缺口（缺什么、为什么重要、需要何种原始来源）；Orchestrator 仅在剩余共享预算/时间内派已有 Scout 定向补查，复用原文，默认最多一次补查交接。预算不足或无法取得时直接交稿并列缺口，不新增必经角色，不无限研究。
末尾附“数据缺口”：逐项写缺指标或证据、最近可得截至日（未知就写未知）、对结论影响、建议补充来源。同步写入 draft.gaps 的简短条目。不强求8–10张表，不把缺数据写成没有变化，不因局部缺失挡住整份报告。''',
      'evaluation':'按本轮实际行业与需求核查已选择板块的任务完成度、表文数值/单位/量级/日期一致性、百分比与基点、事实与预测类别及公司适用条件。对照 report_data 原始值和代码计算结果，再按 source_id/locator 核验来源；计算正确不等于输入真实。缺失但诚实披露不是事实错误；说明缺口对任务完成度的影响，不要求为了满表补数字。保留 report_quote 与原文依据，不添加 Auditor。'}
