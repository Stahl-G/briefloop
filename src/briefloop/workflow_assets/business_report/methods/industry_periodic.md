行业定期方法：按本期覆盖窗口研究价格成本与供应链、需求竞争、政策融资及用户指定重点，按实际相关性取舍；既有模板和明确章节优先。比较本期与上期、预期或关键节点，解释对目标组织的影响。区分报告发布日期、事件发生期与指标截至日。
价格比较保留规格、地区、币种、税口径和交付期；政策保留提案或生效状态与适用对象；规模指标区分实际、预测、公司指引和一致预期；金融变化区分百分比与基点。融资、开工、订单、投产和交付按来源所指阶段表述。
数据进入 prepare-report-data：report_data 仅保存原始 records，derived 由工具生成。record 包含 metric、unit、current、current_date、source_id；预测、指引和一致预期另含 category、as_of。计算变化时提供 previous、previous_date、previous_unit、previous_tax_basis、previous_category，并仅在可比时设置 comparable=true；保存具体原文 locator。缺日期或口径不一致不得猜测补齐。图表采用有来源的数据和确定计算，图表数量服从分析需要。
先使用已上传研报、内部材料和公开原文，重要缺口交给主 Agent 在剩余共享预算内定向补查。历史报告只学组织和深度，旧事实须取得本期依据。预算耗尽仍未完成的研究按真实状态保存。
