# BriefLoop v0.6.1 唯一引用索引

**Scope**：`docs/briefloop-architecture-reference-v0.6.1.md`、`docs/briefloop-architecture-reference-v0.6.1.en.md` 及其测试设计附录。  \
**Selection source**：`docs/tech-report-v0.6.1/reference-screening.md`。  \
**Policy**：本索引只收录本版实际使用的来源；链接只证明可定位性，`supports` 才说明允许的论证范围。

## 学术论文与预印本

### P01 — LIFE-HARNESS

- **title**: [Adapting the Interface, Not the Model: Runtime Harness Adaptation for Deterministic LLM Agents](https://arxiv.org/abs/2605.22166)
- **author_or_organization**: Tianshi Xu; Huifeng Wen; Meng Li
- **year_or_version**: 2026; arXiv:2605.22166v2
- **type**: preprint
- **status**: current
- **supports**: 冻结模型之外的运行时接口可被独立优化；确定性任务可提供清晰奖励。
- **does_not_support**: BriefLoop 已提升开放域简报质量，或同一方法可无损迁移到主观知识工作。
- **used_in**: §1.5; §10.1

### P02 — Self-Harness

- **title**: [Self-Harness: Harnesses That Improve Themselves](https://arxiv.org/abs/2606.09498)
- **author_or_organization**: Hangfan Zhang; Shao Zhang; Kangcong Li; Chen Zhang; Yang Chen; Yiqun Zhang; Lei Bai; Shuyue Hu
- **year_or_version**: 2026; arXiv:2606.09498v1
- **type**: preprint
- **status**: current
- **supports**: 失败挖掘、有界 harness 提案以及域内/留出回归的研究路径。
- **does_not_support**: 智能体可以自批修改活动控制面，或 BriefLoop 已交付自我改进 harness。
- **used_in**: §1.5; §10.1

### P05 — Meta-Harness

- **title**: [Meta-Harness: End-to-End Optimization of Model Harnesses](https://arxiv.org/abs/2603.28052)
- **author_or_organization**: Yoonho Lee; Roshen Nair; Qizheng Zhang; Kangwook Lee; Omar Khattab; Chelsea Finn
- **year_or_version**: 2026; arXiv:2603.28052v1
- **type**: preprint
- **status**: current
- **supports**: harness 可以作为端到端优化对象，且需要把模型、工具和环境共同纳入评估。
- **does_not_support**: 任何候选 harness 都可绕过权限、回归或人工批准直接生效。
- **used_in**: §1.5; §10.1

### P06 — DRA Multi-Turn

- **title**: [Multi-Turn Evaluation of Deep Research Agents Under Process-Level Feedback](https://arxiv.org/abs/2606.09748)
- **author_or_organization**: Rishabh Sabharwal; Hongru Wang; Amos Storkey; Jeff Z. Pan
- **year_or_version**: 2026; arXiv:2606.09748v1
- **type**: preprint / SCALE-ICML 2026 workshop paper
- **status**: current
- **supports**: 过程级反馈可带来单轮改善，多轮修订也可能让先前满足的条件回归。
- **does_not_support**: BriefLoop 的指导体现率、质量提升幅度或跨模型稳定性。
- **used_in**: §10.2

### P07 — CHAP

- **title**: [Collaborative Human-Agent Protocol (CHAP)](https://arxiv.org/abs/2606.09751)
- **author_or_organization**: Arsalan Shahid; Gordon Suttie; Philip Black
- **year_or_version**: 2026; arXiv:2606.09751v2
- **type**: preprint
- **status**: current
- **supports**: 以工作空间、任务、工件和追加式证据日志组织可审计人机协作。
- **does_not_support**: BriefLoop 与 CHAP 协议兼容，或采用同一 schema。
- **used_in**: §10.3

### P08 — Precision Is Not Faithfulness

- **title**: [Precision Is Not Faithfulness: Coverage-Aware Evaluation of Grounded Generation with a Complete Oracle](https://arxiv.org/abs/2606.09376)
- **author_or_organization**: Juan S. Santillana
- **year_or_version**: 2026; arXiv:2606.09376v2
- **type**: preprint
- **status**: current
- **supports**: 单一精度指标会奖励少说；grounded generation 评估需要覆盖维度。
- **does_not_support**: BriefLoop 已达到完整世界召回率，或机械覆盖检查等同于语义充分性。
- **used_in**: §6.4; §10.4

### P09 — AutoGen

- **title**: [AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation](https://arxiv.org/abs/2308.08155)
- **author_or_organization**: Qingyun Wu et al.
- **year_or_version**: 2023; arXiv:2308.08155v2
- **type**: preprint
- **status**: current
- **supports**: 可定制的可对话智能体能够通过多智能体通信构建不同复杂度的应用。
- **does_not_support**: 多智能体对话本身提供阶段事务、证据支持或交付权威。
- **used_in**: §10.5

### P10 — CAMEL

- **title**: [CAMEL: Communicative Agents for “Mind” Exploration of Large Language Model Society](https://arxiv.org/abs/2303.17760)
- **author_or_organization**: Guohao Li; Hasan Abed Al Kader Hammoud; Hani Itani; Dmitrii Khizbullin; Bernard Ghanem
- **year_or_version**: 2023; arXiv:2303.17760v2
- **type**: academic paper / NeurIPS 2023
- **status**: current
- **supports**: 角色扮演与 inception prompting 是多智能体协作的一条代表性路径。
- **does_not_support**: 角色一致性自动产生控制面不变量或可审计发布流程。
- **used_in**: §10.5

### P11 — MetaGPT

- **title**: [MetaGPT: Meta Programming for A Multi-Agent Collaborative Framework](https://arxiv.org/abs/2308.00352)
- **author_or_organization**: Sirui Hong et al.
- **year_or_version**: 2023–2024; arXiv:2308.00352v7
- **type**: academic paper / preprint
- **status**: current
- **supports**: 将标准作业流程编码进多角色协作可以形成结构化流水线。
- **does_not_support**: SOP 提示序列等同于由 schema、transaction 和 event log 强制的治理。
- **used_in**: §10.5

### P12 — FActScore

- **title**: [FActScore: Fine-grained Atomic Evaluation of Factual Precision in Long Form Text Generation](https://aclanthology.org/2023.emnlp-main.741/)
- **author_or_organization**: Sewon Min et al.
- **year_or_version**: 2023; EMNLP 2023:12076–12100; DOI 10.18653/v1/2023.emnlp-main.741
- **type**: peer-reviewed conference paper
- **status**: current
- **supports**: 长文本事实性可通过原子事实分解并逐项核对可靠知识来源。
- **does_not_support**: 原子化本身完成来源检索、支持判断或真理证明。
- **used_in**: §10.4; §10.7.2; 附录 E

### P13 — ALCE

- **title**: [Enabling Large Language Models to Generate Text with Citations](https://arxiv.org/abs/2305.14627)
- **author_or_organization**: Tianyu Gao; Howard Yen; Jiatong Yu; Danqi Chen
- **year_or_version**: 2023; arXiv:2305.14627v2; EMNLP 2023
- **type**: academic paper
- **status**: current
- **supports**: 引用生成需要分别评估流畅度、正确性和引用质量，完整支持仍是独立问题。
- **does_not_support**: 出现链接或引用标记就说明声明得到语义支持。
- **used_in**: §10.4; §10.7.2; 附录 E

### P14 — G-Eval

- **title**: [G-Eval: NLG Evaluation using GPT-4 with Better Human Alignment](https://aclanthology.org/2023.emnlp-main.153/)
- **author_or_organization**: Yang Liu; Dan Iter; Yichong Xu; Shuohang Wang; Ruochen Xu; Chenguang Zhu
- **year_or_version**: 2023; EMNLP 2023:2511–2522; DOI 10.18653/v1/2023.emnlp-main.153
- **type**: peer-reviewed conference paper
- **status**: current
- **supports**: 结构化 LLM 评审可用于开放式文本质量评估，并可能提高与人类判断的相关性。
- **does_not_support**: LLM 评审无偏、可取代人类或可以获得发布权威。
- **used_in**: 附录 E

### P15 — MT-Bench

- **title**: [Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena](https://arxiv.org/abs/2306.05685)
- **author_or_organization**: Lianmin Zheng et al.
- **year_or_version**: 2023; arXiv:2306.05685v4; NeurIPS 2023 Datasets and Benchmarks
- **type**: academic paper
- **status**: current
- **supports**: LLM-as-a-judge 具有规模优势，但存在位置、冗长、自偏好和推理能力等偏差。
- **does_not_support**: 单一模型评审分数足以证明 BriefLoop 输出质量。
- **used_in**: 附录 E

### P16 — Self-Refine

- **title**: [Self-Refine: Iterative Refinement with Self-Feedback](https://proceedings.neurips.cc/paper_files/paper/2023/hash/91edff07232fb1b55a505a9e9f6c0ff3-Abstract-Conference.html)
- **author_or_organization**: Aman Madaan et al.
- **year_or_version**: 2023; NeurIPS 2023 Main Conference Track
- **type**: peer-reviewed conference paper
- **status**: current
- **supports**: 同一模型可通过反馈—修订循环改善特定任务输出。
- **does_not_support**: 模型自反馈可替代外部证据、确定性门禁或人工批准。
- **used_in**: §10.6

### P17 — Reflexion

- **title**: [Reflexion: Language Agents with Verbal Reinforcement Learning](https://proceedings.neurips.cc/paper_files/paper/2023/hash/1b44b878bb782e6954cd888628510e90-Abstract-Conference.html)
- **author_or_organization**: Noah Shinn; Federico Cassano; Ashwin Gopinath; Karthik Narasimhan; Shunyu Yao
- **year_or_version**: 2023; NeurIPS 2023 Main Conference Track
- **type**: peer-reviewed conference paper
- **status**: current
- **supports**: 语言反馈和情节记忆可以影响后续试次的决策。
- **does_not_support**: 反思记忆天然具备单写者、冻结、可逆生效或跨运行审计属性。
- **used_in**: §10.6

### P19 — AI Agents That Matter

- **title**: [AI Agents That Matter](https://arxiv.org/abs/2407.01502)
- **author_or_organization**: Sayash Kapoor; Benedikt Stroebl; Zachary S. Siegel; Nitya Nadgir; Arvind Narayanan
- **year_or_version**: 2024; arXiv:2407.01502v1
- **type**: preprint
- **status**: current
- **supports**: 智能体评测应控制成本、避免基准捷径、区分模型与下游开发者需求并提高复现性。
- **does_not_support**: BriefLoop 已优于直接提示、已改善内容质量或其控制面指标等同于真实业务效用。
- **used_in**: 附录 E

### P20 — ResearchLoop

- **title**: [ResearchLoop: An Evidence-Gated Control Plane for AI-Assisted Research](https://arxiv.org/abs/2605.28282)
- **author_or_organization**: Yihan Xia; Taotao Wang
- **year_or_version**: 2026; arXiv:2605.28282v1
- **type**: technical report / preprint
- **status**: current
- **supports**: 研究问题、证据对象、声明账本和论文绑定可以成为持久项目状态；证据门禁应外置。
- **does_not_support**: BriefLoop 与 ResearchLoop 使用相同协议，或 ResearchLoop 的实验结果可外推到企业简报。
- **used_in**: §10.4

### P21 — EvoMAS

- **title**: [EvoMAS: Evolutionary Generation of Multi-Agent Systems](https://arxiv.org/abs/2602.06511)
- **author_or_organization**: Yuntong Hu; Yuting Zhang; Matthew Trager; Yi Zhang; Shuo Yang; Wei Xia; Stefano Soatto
- **year_or_version**: 2026; arXiv:2602.06511v4; ICML 2026
- **type**: academic paper
- **status**: current
- **supports**: 执行轨迹可指导在结构化配置空间中进行多智能体候选的变异、交叉和选择。
- **does_not_support**: BriefLoop 已实现自动拓扑进化，或生成配置可以不经确定性验证和人类批准直接改变未来运行。
- **used_in**: §10.5; §11.4

### P22 — Knowledge Conflicts for LLMs: A Survey

- **title**: [Knowledge Conflicts for LLMs: A Survey](https://aclanthology.org/2024.emnlp-main.486/)
- **author_or_organization**: Rongwu Xu; Zehan Qi; Zhijiang Guo; Cunxiang Wang; Hongru Wang; Yue Zhang; Wei Xu
- **year_or_version**: 2024; EMNLP 2024; DOI 10.18653/v1/2024.emnlp-main.486
- **type**: peer-reviewed conference paper
- **status**: current
- **supports**: 知识冲突可区分为 context-memory、inter-context 与 intra-memory 三类，并且是 LLM 融合参数知识和上下文知识时的系统性问题。
- **does_not_support**: BriefLoop 已检测、区分或解决这三类冲突，或某种特定控制架构必然优于 RAG。
- **used_in**: §10.7.2; Appendix E

### P23 — StreamingQA

- **title**: [StreamingQA: A Benchmark for Adaptation to New Knowledge over Time in Question Answering Models](https://proceedings.mlr.press/v162/liska22a.html)
- **author_or_organization**: Adam Liska; Tomas Kocisky; Elena Gribovskaya; Tayfun Terzi; Eren Sezener; Devang Agrawal; Cyprien De Masson D'Autume; Tim Scholtes; Manzil Zaheer; Susannah Young; Ellen Gilsenan-Mcmahon; Sophia Austin; Phil Blunsom; Angeliki Lazaridou
- **year_or_version**: 2022; ICML 2022; PMLR 162:13604–13622
- **type**: peer-reviewed conference paper
- **status**: current
- **supports**: 在 14 年带时间戳新闻上，向检索空间加入新文章有助快速适应，但底层 LM 过时的系统仍弱于同步更新参数模型的系统。
- **does_not_support**: 所有 2026 年前沿模型都会以相同幅度失败，或该 QA benchmark 可直接给出周期性简报错误率。
- **used_in**: §10.7.2; Appendix E

### P24 — DYNAMICQA

- **title**: [DYNAMICQA: Tracing Internal Knowledge Conflicts in Language Models](https://aclanthology.org/2024.findings-emnlp.838/)
- **author_or_organization**: Sara Vera Marjanovic; Haeun Yu; Pepa Atanasova; Maria Maistro; Christina Lioma; Isabelle Augenstein
- **year_or_version**: 2024; Findings of EMNLP 2024; DOI 10.18653/v1/2024.findings-emnlp.838
- **type**: peer-reviewed conference paper
- **status**: current
- **supports**: 动态事实比稳定事实表现出更多参数记忆内部冲突，且存在内部冲突的事实更难被新上下文更新。
- **does_not_support**: 一次采样即可可靠检测任意模型的内部冲突，或 BriefLoop 已具备 intra-memory conflict 检测器。
- **used_in**: §10.7.2

### P25 — Tug-of-War between Knowledge

- **title**: [Tug-of-War between Knowledge: Exploring and Resolving Knowledge Conflicts in Retrieval-Augmented Language Models](https://aclanthology.org/2024.lrec-main.1466/)
- **author_or_organization**: Zhuoran Jin; Pengfei Cao; Yubo Chen; Kang Liu; Xiaojian Jiang; Jiexin Xu; Li Qiuxia; Jun Zhao
- **year_or_version**: 2024; LREC-COLING 2024:16867–16878
- **type**: peer-reviewed conference paper
- **status**: current
- **supports**: 受控实验中，一些检索增强模型即使得到正确外部证据仍坚持错误内部记忆，并表现出多数规则和确认偏差。
- **does_not_support**: 所有商业模型具有相同偏差，来源等级足以自动消除冲突，或论文缓解方法可直接迁移到 BriefLoop 的黑箱模型环境。
- **used_in**: §10.7.2

### P26 — QACC

- **title**: [Open Domain Question Answering with Conflicting Contexts](https://aclanthology.org/2025.findings-naacl.99/)
- **author_or_organization**: Siyi Liu; Qiang Ning; Kishaloy Halder; Zheng Qi; Wei Xiao; Phu Mon Htut; Yi Zhang; Neha Anna John; Bonan Min; Yassine Benajiba; Dan Roth
- **year_or_version**: 2025; Findings of NAACL 2025; DOI 10.18653/v1/2025.findings-naacl.99
- **type**: peer-reviewed conference paper
- **status**: current
- **supports**: 在 QACC 的开放域 Google Search 设置下，多达约 25% 的无歧义问题可能检索到冲突上下文，且所测模型处理这类信息仍有限。
- **does_not_support**: 25% 的新闻周报事实存在冲突，BriefLoop 来源集合具有相同比例，或 Google Search 代表企业内部数据环境。
- **used_in**: §10.7.2; Appendix E

### P27 — When Facts Change

- **title**: [When Facts Change: Temporal Knowledge Conflict Resolution in LLMs](https://aclanthology.org/2026.findings-acl.103/)
- **author_or_organization**: Jonas Wallat; Wolfgang Nejdl; Sandipan Sikdar
- **year_or_version**: 2026; Findings of ACL 2026; DOI 10.18653/v1/2026.findings-acl.103
- **type**: peer-reviewed conference paper
- **status**: current
- **supports**: 在该时间冲突 benchmark 中，模型对事实变化的口头识别很少稳定传递到最终预测；提示事实可变性增加时间表述但未提高事实准确率。
- **does_not_support**: 所有 prompt 方法均无效，BriefLoop 的确定性控制可以自动决定动态事实真值，或该 benchmark 直接代表新闻、监管文件和企业周报。
- **used_in**: §10.7.2

### P28 — Who's Who

- **title**: [Who's Who: Large Language Models Meet Knowledge Conflicts in Practice](https://aclanthology.org/2024.findings-emnlp.593/)
- **author_or_organization**: Quang Hieu Pham; Hoang Ngo; Anh Tuan Luu; Dat Quoc Nguyen
- **year_or_version**: 2024; Findings of EMNLP 2024; DOI 10.18653/v1/2024.findings-emnlp.593
- **type**: peer-reviewed conference paper
- **status**: current
- **supports**: 检索上下文冲突会削弱 RAG 表现；缺乏充分裁决依据时，透明告知冲突比模型基于自身偏好静默选择更稳健。
- **does_not_support**: 所有冲突都必须交给人，展示多个答案就等于完成可信治理，或 BriefLoop 已自动发现全部冲突。
- **used_in**: §10.7.2; Appendix E

### P29 — Harmful Factuality

- **title**: [Harmful Factuality: LLMs Correcting What They Shouldn't](https://aclanthology.org/2026.findings-eacl.46/)
- **author_or_organization**: Mingchen Li; Hanzhi Zhang; Heng Fan; Junhua Ding; Yunhe Feng
- **year_or_version**: 2026; Findings of EACL 2026; DOI 10.18653/v1/2026.findings-eacl.46
- **type**: peer-reviewed conference paper
- **status**: current
- **supports**: 模型试图纠正来源时，可能产生事实正确但不忠实于输入的输出；事实正确性与来源忠实度需要分开测量。
- **does_not_support**: 来源一定比模型记忆正确，系统不应核查来源本身，或 Prompt 可以永久消除此问题。
- **used_in**: §5.3; §10.7.2; Appendix E

### P30 — Time-Aware Language Models

- **title**: [Time-Aware Language Models as Temporal Knowledge Bases](https://aclanthology.org/2022.tacl-1.15/)
- **author_or_organization**: Bhuwan Dhingra; Jeremy R. Cole; Julian Martin Eisenschlos; Daniel Gillick; Jacob Eisenstein; William W. Cohen
- **year_or_version**: 2022; TACL 10:257–273; DOI 10.1162/tacl_a_00459
- **type**: peer-reviewed journal paper
- **status**: current
- **supports**: 许多事实具有时间有效域，而语言模型通常训练于时间快照；显式时间建模是可研究的缓解方向。
- **does_not_support**: 该方法直接适用于黑箱企业智能体，或添加时间戳即可自动解决来源冲突与发布裁决。
- **used_in**: §5.3; §10.7.2

### P31 — FreshLLMs

- **title**: [FreshLLMs: Refreshing Large Language Models with Search Engine Augmentation](https://aclanthology.org/2024.findings-acl.813/)
- **author_or_organization**: Tu Vu; Mohit Iyyer; Xuezhi Wang; Noah Constant; Jerry Wei; Jason Wei; Chris Tar; Yun-Hsuan Sung; Denny Zhou; Quoc Le; Thang Luong
- **year_or_version**: 2024; Findings of ACL 2024; DOI 10.18653/v1/2024.findings-acl.813
- **type**: peer-reviewed conference paper
- **status**: current
- **supports**: 经组织的搜索结果可以改善快速变化知识和错误前提上的问答表现，证明搜索增强有实际价值。
- **does_not_support**: 搜索增强自动完成来源冲突、版本取代或企业发布裁决，或其 benchmark 改善可直接外推为 BriefLoop 的质量提升。
- **used_in**: §10.7.2

### P32 — Astute RAG

- **title**: [Astute RAG: Overcoming Imperfect Retrieval Augmentation and Knowledge Conflicts for Large Language Models](https://aclanthology.org/2025.acl-long.1476/)
- **author_or_organization**: Fei Wang; Xingchen Wan; Ruoxi Sun; Jiefeng Chen; Sercan O Arik
- **year_or_version**: 2025; ACL 2025; DOI 10.18653/v1/2025.acl-long.1476
- **type**: peer-reviewed conference paper
- **status**: current
- **supports**: 冲突感知、来源感知的后检索整合是活跃研究方向，并能在论文 benchmark 中改善不完美检索下的表现。
- **does_not_support**: 知识冲突已被普遍解决，benchmark 中的答案选择机制可承担企业发布权威，或 BriefLoop 必须采用 Astute RAG。
- **used_in**: §10.7.2

### P33 — Credibility-Aware Generation

- **title**: [Not All Contexts Are Equal: Teaching LLMs Credibility-aware Generation](https://aclanthology.org/2024.emnlp-main.1109/)
- **author_or_organization**: Ruotong Pan; Boxi Cao; Hongyu Lin; Xianpei Han; Jia Zheng; Sirui Wang; Xunliang Cai; Le Sun
- **year_or_version**: 2024; EMNLP 2024; DOI 10.18653/v1/2024.emnlp-main.1109
- **type**: peer-reviewed conference paper
- **status**: current
- **supports**: 外部可信度可被建模为显式生成信号，并在论文 benchmark 中减轻瑕疵上下文的影响。
- **does_not_support**: 来源等级自动决定真相，可信度信号可取代声明级支持判断，或 BriefLoop 当前已经训练可信度感知模型。
- **used_in**: §5.3; §10.7.2

### P34 — Don't Ask the LLM to Track Freshness

- **title**: [Don't Ask the LLM to Track Freshness: A Deterministic Recipe for Memory Conflict Resolution](https://arxiv.org/abs/2606.01435)
- **author_or_organization**: Vikas Reddy; Sumanth Challaram
- **year_or_version**: 2026-05-31; arXiv:2606.01435v1
- **type**: preprint
- **status**: current-preprint
- **supports**: 在具有明确版本序号、可全序的 current-value 冲突上，语义候选提取加确定性聚合可优于自由文本 LLM 判断；长上下文中的差距说明检索后的组装与版本比较可能成为独立故障点。
- **does_not_support**: 一般真相裁决、最新来源自动获得权威、部分序取代、企业发布治理或 BriefLoop 性能；主要结果是 prompt、格式、温度和 resolver 共同变化的管线级效应，且 45 条 LongMemEval 样本上的确定性管线未优于 LLM 判断。
- **used_in**: 本版修订; §10.7.2; 附录 E

### P35 — ConflictRAG

- **title**: [ConflictRAG: Detecting and Resolving Knowledge Conflicts in Retrieval-Augmented Generation](https://arxiv.org/abs/2605.17301)
- **author_or_organization**: Chenyu Wang; Yueyuan Li; Yingmin Liu; Yang Shu
- **year_or_version**: 2026-06-08; arXiv:2605.17301v2; submitted to IEEE SMC 2026
- **type**: preprint
- **status**: current-preprint; submitted, acceptance not stated
- **supports**: 文档间与参数—上下文冲突可在生成前经过显式检测、分类和类型化处置，并在输出中保留来源归属与冲突说明；可作为冲突感知 RAG 的邻近方法与计划基线。
- **does_not_support**: 自动来源权威、近期性等于有效性、检索证据总应胜出、CARS 是中立发布指标，或 BriefLoop 的实现与性能已经得到验证。
- **used_in**: 本版修订; §10.7.2; 附录 E

## 理论与架构基础

### T01 — Workflow Patterns

- **title**: [Workflow Patterns](https://doi.org/10.1023/A:1022883727209)
- **author_or_organization**: Wil M. P. van der Aalst; Arthur H. M. ter Hofstede; Bartek Kiepuszewski; Alistair P. Barros
- **year_or_version**: 2003; Distributed and Parallel Databases 14:5–51
- **type**: peer-reviewed journal article
- **status**: current
- **supports**: 工作流控制流可以用可复用模式描述，并比较其表达能力。
- **does_not_support**: BriefLoop 的具体状态机正确或完整。
- **used_in**: §2.3

### T02 — Blackboard Architecture

- **title**: [The Blackboard Model of Problem Solving and the Evolution of Blackboard Architectures](https://doi.org/10.1609/aimag.v7i2.537)
- **author_or_organization**: H. Penny Nii
- **year_or_version**: 1986; AI Magazine 7(2)
- **type**: peer-reviewed journal article
- **status**: current
- **supports**: 专门知识源可围绕共享、逐步演化的问题状态协作。
- **does_not_support**: 共享文件天然安全、不可变或具备唯一权威写者。
- **used_in**: §2.3

### T03 — Design by Contract

- **title**: [Applying “Design by Contract”](https://ieeexplore.ieee.org/document/161279/)
- **author_or_organization**: Bertrand Meyer
- **year_or_version**: 1992; Computer 25(10)
- **type**: peer-reviewed journal article
- **status**: current
- **supports**: 通过前置条件、后置条件和不变量明确组件责任。
- **does_not_support**: 自然语言合约无需 schema、validator 或测试就能被机器强制。
- **used_in**: §2.3

### T06 — W3C PROV-DM

- **title**: [PROV-DM: The PROV Data Model](https://www.w3.org/TR/prov-dm/)
- **author_or_organization**: W3C; editors Luc Moreau and Paolo Missier
- **year_or_version**: W3C Recommendation, 2013-04-30
- **type**: technical standard
- **status**: current
- **supports**: 用实体、活动、责任主体和派生关系描述 provenance 的通用词汇。
- **does_not_support**: BriefLoop 已通过 W3C 一致性测试或完整实现 PROV-DM。
- **used_in**: §2.3; §3.2

## 技术文章与工程案例

### E01 — OpenAI Tax AI

- **title**: [Building Self-Improving Tax Agents with Codex](https://openai.com/index/building-self-improving-tax-agents-with-codex/)
- **author_or_organization**: Aravind Srinivasan; Samay Shamdasani; Arthur Fernandes Araujo; John de Wasseige; OpenAI and Thrive Holdings
- **year_or_version**: 2026-05-27
- **type**: first-party engineering case study
- **status**: current
- **supports**: 专家修正、生产追踪、定制评测、有界工程任务、回归验证和人工评审组成生产改进闭环。
- **does_not_support**: BriefLoop 已实现同等生产闭环、取得同类性能提升或能够自主上线修复。
- **used_in**: §10.7.6

### E10 — Anthropic multi-agent guidance

- **title**: [Building Multi-Agent Systems: When and How to Use Them](https://claude.com/blog/building-multi-agent-systems-when-and-how-to-use-them)
- **author_or_organization**: Anthropic
- **year_or_version**: 2026-01-23
- **type**: first-party engineering article
- **status**: current
- **supports**: 多智能体最适合上下文隔离、并行探索和专业化，其他情况下协调成本可能超过收益。
- **does_not_support**: BriefLoop 的角色拓扑必然优于单智能体，或任何性能改善幅度。
- **used_in**: §10.5; §10.7.4

### E19 — Weng harness synthesis

- **title**: [Harness Engineering for Self-Improvement](https://lilianweng.github.io/posts/2026-07-04-harness/)
- **author_or_organization**: Lilian Weng
- **year_or_version**: 2026-07-04
- **type**: research synthesis / technical article
- **status**: current
- **supports**: harness 的领域定义以及评估器、权限、奖励投机、记忆生命周期和人类监督等风险综合。
- **does_not_support**: BriefLoop 评测结果；不替代其综述中原始研究的实验引用。
- **used_in**: §1.5; §10.1

### E20 — Loop Engineering

- **title**: [Loop Engineering](https://addyo.substack.com/p/loop-engineering)
- **author_or_organization**: Addy Osmani
- **year_or_version**: 2026-06-08
- **type**: engineering article
- **status**: current
- **supports**: “从提示智能体转向设计提示智能体的系统”这一术语与工程范式归因。
- **does_not_support**: BriefLoop 的实现正确性、控制面充分性或实验结果。
- **used_in**: §1.6; §10.7.3

### E21 — Building Effective Agents

- **title**: [Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents)
- **author_or_organization**: Anthropic
- **year_or_version**: 2024-12-19
- **type**: first-party engineering article
- **status**: current
- **supports**: workflow 与 agent 的架构区分，以及固定子任务、清晰输入输出和程序化检查的工程模式。
- **does_not_support**: BriefLoop 的具体合约、门禁或多智能体拓扑已经得到独立验证。
- **used_in**: §1.3; §10.7.1; §10.7.3

### E22 — Hermes Agent memory

- **title**: [Persistent Memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory)
- **author_or_organization**: Nous Research
- **year_or_version**: rolling documentation; accessed 2026-07-14
- **type**: project documentation
- **status**: current
- **supports**: `USER.md`/`MEMORY.md` 式人类可读持久记忆、容量管理和可选写入批准的设计表面。
- **does_not_support**: BriefLoop 的 per-run freeze、manifest hash、单写者、未来运行生效或账本链由 Hermes 原生提供。
- **used_in**: §10.6

## 索引完整性规则

1. 正文引用使用稳定 ID（例如 `[P21]`），不得只写作者姓氏而缺失索引条目。
2. 每个索引条目必须至少在一个 `used_in` 位置出现；未使用条目应删除或降回筛选记录。
3. `status` 表示本版是否仍在使用，不表示论文结论已被复现。
4. 产品与工程文章只能支撑一手实践描述，不能替代学术验证或 BriefLoop 自身测试。
5. v0.6.1 中英文 Markdown 与 HTML 共享这一组 `ref_id`；译文不得另起一套编号。
