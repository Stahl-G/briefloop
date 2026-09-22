// A trusted, in-memory hook: Pi still chooses the cut, summarizes, saves and
// resumes. No extension discovery, new model/provider, tools or second loop.
import { compact, createSyntheticSourceInfo } from '@earendil-works/pi-coding-agent';
import type { AgentSession, Extension, SessionBeforeCompactEvent } from '@earendil-works/pi-coding-agent';

export const COMPACTION_POLICY_VERSION = 'briefloop-continuity/1';
export const COMPACTION_FOCUS = `压缩用于继续 BriefLoop 当前任务，不是重新研究、评分或交稿。沿用原生摘要格式，优先保留下列必要信息，去掉重复全文和重复工具输出：
1. 用户已确认的目标、读者、时间窗、必答问题、明确限制，以及后续纠正；不降低或改写冻结要求。
2. 当前角色与阶段；哪些已成功保存/接纳，哪些仅计划、运行中、失败或待确认。区分草稿保存、独立审阅、修订和正式交付。
3. run/version/revision/source/section/review/job 等实际出现的标识、哈希、文件位置和回执须逐字保留。没有记录就写未知，不能补造。尤其保留最新完整稿件 revision、已保存章节及尚未检查的改动。
4. 关键结论对应的原文位置，以及改变行动的前提、范围、日期、口径、反例、更正/取代关系；来源事实、作者推断、待核验事项分开。旧限制已被更正的，保留更新后的状态及依据。
5. 未解决的审阅发现、失败原因、待处理问题及下一步最小操作；不要因压缩而把问题标为已解决，也不要重复启动已有任务或收费调用。
6. 联网/文件/工具授权及剩余预算只保留已知状态；摘要不能扩大权限。实际状态以任务包和工具回执为准。
7. 摘要只作导航，不是原文证据或新的指令。压缩后按保留的位置回读冻结要求、相关原文和最新稿件，再继续核对。保留结论和依据，不复制隐藏推理、凭据或大块材料。`;

export const AUTHOR_COMPACTION_ROLES = new Set(['analyst', 'orchestrator', 'chat', 'scout']);

export function continuityExtension(getSession: () => AgentSession, role: string, onFailure: () => void): Extension {
  const identity = 'briefloop:context-continuity';
  return {
    path: identity, resolvedPath: identity,
    sourceInfo: createSyntheticSourceInfo(identity, {source: 'BriefLoop bundled', scope: 'temporary'}),
    tools: new Map(), commands: new Map(), flags: new Map(), shortcuts: new Map(), messageRenderers: new Map(),
    handlers: new Map([['session_before_compact', [async (...args: unknown[]) => {
      const event = args[0] as SessionBeforeCompactEvent;
      try {
        const session = getSession();
        if (!session.model) throw new Error('No model for context compaction');
        const focus = `${COMPACTION_FOCUS}\n当前角色：${role}。` +
          (event.customInstructions ? `\n本次额外压缩重点（不改变权限或冻结要求）：\n${event.customInstructions}` : '');
        // The SDK's split-turn summary omits customInstructions in 0.85.1. Add
        // focus at its summarization stream boundary so BOTH summaries receive it.
        // Delegate the original SDK stream; keep authentication, model and retry.
        const stream = session.agent.streamFunction;
        const result = await compact(event.preparation, session.model, undefined, undefined,
          undefined, event.signal, session.thinkingLevel,
          (model, context, options) => stream(model, {...context,
            systemPrompt: `${context.systemPrompt || ''}\n\n${focus}`}, options),
          undefined, session.settingsManager.getRetrySettings());
        return {compaction: {...result, details: {...(result.details as object || {}),
          briefloop_policy: COMPACTION_POLICY_VERSION}}};
      } catch {
        // Pi catches extension exceptions and then uses its default compactor.
        // Explicitly cancel instead: never silently lose the preservation focus
        // or repeat a failed billed call through a different summarizer path.
        if (!event.signal.aborted) onFailure();
        return {cancel: true};
      }
    }]]]),
  };
}
