// BriefLoop DeepSeek Harness (DSH) operator plugin — host half.
//
// Experimental 0.16.0 official form. Every tool calls the `briefloop` CLI
// through the shell service and NEVER opens `briefloop.db` or writes the
// ControlStore directly: the deterministic Python/SQLite layer owns every
// authority decision, and this plugin is a comfort layer only.
//
// Load it in a DSH session via the dynamic Cordis plugin channel (host half
// only; no client half is required). Set BRIEFLOOP_BIN to the installed
// `briefloop` executable path, or leave it as `'briefloop'` to resolve from
// the host PATH.

const BRIEFLOOP_BIN = 'briefloop'

return {
  name: 'briefloop-dsh-operator',
  inject: ['shell'],
  apply(ctx) {
    const shell = ctx.shell
    const sandboxPolicy = ctx.get('sandboxPolicy')
    const confines = shell.sandboxMode !== undefined

    const q = (value) => {
      const s = String(value)
      return "'" + s.replace(/'/g, "'\\''") + "'"
    }

    const runCommand = async (command, exec, timeoutMs) => {
      const request = {
        command,
        timeoutMs,
        stdoutMaxBytes: 256 * 1024,
      }
      if (exec && exec.signal !== undefined) request.signal = exec.signal
      if (confines && sandboxPolicy !== undefined && exec && exec.agent && exec.agent.session) {
        request.sandboxPolicy = await sandboxPolicy.resolve({ session: exec.agent.session })
      }
      const result = await shell.run(shell.resolve(request))
      const stdout = result.stdout ? result.stdout.text || '' : ''
      const stderr = result.stderr ? result.stderr.text || '' : ''
      const ok = result.exitCode === 0 && !result.timedOut && !result.aborted
      return {
        ok,
        exitCode: result.exitCode,
        signal: result.signal,
        timedOut: result.timedOut,
        aborted: result.aborted,
        stdout: stdout.trim(),
        stderr: stderr.trim(),
        sandboxDenied: !ok && /sandbox: file access denied/.test(stderr),
      }
    }

    const runCli = (argv, exec, timeoutMs) =>
      runCommand([BRIEFLOOP_BIN, ...argv].join(' '), exec, timeoutMs)

    // Package-private Client RPC: the Web UI launcher button calls this to
    // start a workspace. It runs the CLI launcher only and never opens the
    // ControlStore directly.
    harness.handle('briefloop_start', async (args) => {
      const ws = String((args && args.workspace) || '')
      const rt = (args && args.runtime) === 'codex' ? 'codex' : 'dsh'
      if (!ws) return { ok: false, error: 'workspace path is required' }
      return runCommand(
        [BRIEFLOOP_BIN, 'run', '--workspace', q(ws), '--runtime', rt].join(' '),
        undefined,
        120000,
      )
    })

    const textOut = (_args, value) => {
      const lines = []
      lines.push(value.ok ? 'OK (exit 0)' : 'FAILED (exit ' + value.exitCode + (value.sandboxDenied ? ', sandbox-denied' : '') + ')')
      if (value.stdout) lines.push(value.stdout)
      if (value.stderr) lines.push('STDERR: ' + value.stderr)
      const text = lines.join('\n')
      return [{ type: 'text', text: text.length > 6000 ? text.slice(0, 6000) + '\n...' : text }]
    }

    const outSchema = { type: 'object', additionalProperties: true }
    const workspaceParam = {
      workspace: { type: 'string', description: 'Absolute path to the BriefLoop workspace directory.' },
    }

    const define = (name, description, parameters, argvFn, timeoutMs = 60000) =>
      harness.defineTool({
        name,
        description,
        parameters,
        output: { schema: outSchema, render: textOut },
        execute: async (args, exec) => runCli(argvFn(args), exec, timeoutMs),
      })

    const disposers = [
      // Read-only.
      harness.registerTool(ctx, define(
        'briefloop_version',
        'Print the installed briefloop CLI version. Read-only; never writes the ControlStore.',
        { type: 'object', properties: {} },
        () => ['version'],
        30000,
      )),
      harness.registerTool(ctx, define(
        'briefloop_status',
        'Read-only Store projection (briefloop status --workspace <path> --json). Never writes the ControlStore.',
        { type: 'object', properties: workspaceParam, required: ['workspace'] },
        (a) => ['status', '--workspace', q(a.workspace), '--json'],
      )),
      harness.registerTool(ctx, define(
        'briefloop_runtime_next',
        'Read-only Store-derived CoreRunNextAction (briefloop runtime next). Never writes the ControlStore.',
        { type: 'object', properties: workspaceParam, required: ['workspace'] },
        (a) => ['runtime', 'next', '--workspace', q(a.workspace)],
      )),
      harness.registerTool(ctx, define(
        'briefloop_contract_show',
        'Read-only contract example (briefloop contract show <schema> --example full).',
        {
          type: 'object',
          properties: {
            schema: { type: 'string', description: 'Exact proposal/request schema id.' },
          },
          required: ['schema'],
        },
        (a) => ['contract', 'show', q(a.schema), '--example', 'full'],
      )),
      // Workspace bootstrap.
      harness.registerTool(ctx, define(
        'briefloop_init',
        'Create a demo BriefLoop workspace (briefloop init <workspace> --demo [--runtime codex|dsh]). Non-interactive; demo profile only.',
        {
          type: 'object',
          properties: {
            workspace: { type: 'string', description: 'Target workspace directory.' },
            runtime: { type: 'string', description: 'ControlStore runtime identity: codex (default) or dsh.' },
          },
          required: ['workspace'],
        },
        (a) => ['init', q(a.workspace), '--demo', ...(a.runtime ? ['--runtime', q(a.runtime)] : [])],
      )),
      harness.registerTool(ctx, define(
        'briefloop_runtime_install',
        'Install a runtime kit into a workspace (briefloop runtime install --workspace <w> --runtime <r>).',
        {
          type: 'object',
          properties: {
            workspace: { type: 'string', description: 'Target workspace directory.' },
            runtime: { type: 'string', description: 'codex or dsh.' },
          },
          required: ['workspace', 'runtime'],
        },
        (a) => ['runtime', 'install', '--workspace', q(a.workspace), '--runtime', q(a.runtime)],
        120000,
      )),
      // Runtime driver.
      harness.registerTool(ctx, define(
        'briefloop_runtime_continue',
        'Bounded Store continuation (briefloop runtime continue). Applies only the exact Store-derived next effect.',
        { type: 'object', properties: workspaceParam, required: ['workspace'] },
        (a) => ['runtime', 'continue', '--workspace', q(a.workspace)],
        120000,
      )),
      harness.registerTool(ctx, define(
        'briefloop_runtime_apply',
        'Apply one deterministic or Human decision Store action (briefloop runtime apply --action <path>).',
        {
          type: 'object',
          properties: {
            workspace: { type: 'string', description: 'Workspace directory.' },
            action: { type: 'string', description: 'Path to the frozen runtime_action.json.' },
            human_request: { type: 'string', description: 'Optional path to a bound Human request JSON.' },
          },
          required: ['workspace', 'action'],
        },
        (a) => [
          'runtime', 'apply', '--workspace', q(a.workspace),
          '--action', q(a.action),
          ...(a.human_request ? ['--human-request', q(a.human_request)] : []),
        ],
        120000,
      )),
      harness.registerTool(ctx, define(
        'briefloop_runtime_invocation_start',
        'Start the exact role invocation (briefloop runtime invocation-start --action <path>).',
        {
          type: 'object',
          properties: {
            workspace: { type: 'string', description: 'Workspace directory.' },
            action: { type: 'string', description: 'Path to the frozen runtime_action.json.' },
          },
          required: ['workspace', 'action'],
        },
        (a) => ['runtime', 'invocation-start', '--workspace', q(a.workspace), '--action', q(a.action)],
        120000,
      )),
      harness.registerTool(ctx, define(
        'briefloop_runtime_invocation_validate',
        'Validate a role proposal against its envelope (briefloop runtime invocation-validate --envelope <path>).',
        {
          type: 'object',
          properties: {
            workspace: { type: 'string', description: 'Workspace directory.' },
            envelope: { type: 'string', description: 'Path to role_task_envelope.json.' },
          },
          required: ['workspace', 'envelope'],
        },
        (a) => ['runtime', 'invocation-validate', '--workspace', q(a.workspace), '--envelope', q(a.envelope)],
        120000,
      )),
      harness.registerTool(ctx, define(
        'briefloop_runtime_invocation_accept',
        'Accept a validated role proposal (briefloop runtime invocation-accept --envelope <path>).',
        {
          type: 'object',
          properties: {
            workspace: { type: 'string', description: 'Workspace directory.' },
            envelope: { type: 'string', description: 'Path to role_task_envelope.json.' },
          },
          required: ['workspace', 'envelope'],
        },
        (a) => ['runtime', 'invocation-accept', '--workspace', q(a.workspace), '--envelope', q(a.envelope)],
        120000,
      )),
      harness.registerTool(ctx, define(
        'briefloop_runtime_invocation_fail',
        'Record one value-free allowed invocation failure (briefloop runtime invocation-fail --envelope <path> --reason <reason>).',
        {
          type: 'object',
          properties: {
            workspace: { type: 'string', description: 'Workspace directory.' },
            envelope: { type: 'string', description: 'Path to role_task_envelope.json.' },
            reason: { type: 'string', description: 'Allowed failure reason code.' },
          },
          required: ['workspace', 'envelope', 'reason'],
        },
        (a) => [
          'runtime', 'invocation-fail', '--workspace', q(a.workspace),
          '--envelope', q(a.envelope), '--reason', q(a.reason),
        ],
        120000,
      )),
      harness.registerTool(ctx, harness.defineTool({
        name: 'briefloop_role_dispatch',
        description: 'Read-only: materialize the exact DSH subagent dispatch context for a role envelope (role contract from the matching preset + a ready dispatch prompt). Hand the returned dispatch_prompt to the subagent tool. Never writes the ControlStore.',
        parameters: {
          type: 'object',
          properties: {
            workspace: { type: 'string', description: 'Workspace directory.' },
            envelope: { type: 'string', description: 'Path to role_task_envelope.json (workspace-relative or absolute).' },
          },
          required: ['workspace', 'envelope'],
        },
        output: {
          schema: { type: 'object', additionalProperties: true },
          render: (_args, value) => {
            if (value && value.ok && value.dispatch_prompt) {
              const head = 'preset_id: ' + value.preset_id + ' | role_id: ' + value.role_id
                + ' | dispatch_instruction: ' + value.dispatch_instruction + '\n\n'
              return [{ type: 'text', text: head + value.dispatch_prompt }]
            }
            return [{ type: 'text', text: JSON.stringify(value) }]
          },
        },
        async execute(args, exec) {
          const ws = String(args.workspace)
          const envArg = String(args.envelope)
          const envPath = envArg.startsWith('/') ? envArg : ws + '/' + envArg
          const envRes = await runCommand('cat ' + q(envPath), exec, 30000)
          if (!envRes.ok) return { ok: false, error: 'envelope_read_failed', stderr: envRes.stderr }
          let envelope
          try {
            envelope = JSON.parse(envRes.stdout)
          } catch (_e) {
            return { ok: false, error: 'envelope_unparseable', stdout: envRes.stdout }
          }
          const roleId = envelope.role_id
          const presetId = 'briefloop-' + roleId
          const presetPath = ws + '/.dsh/presets/' + presetId + '/agent.cordis.yml'
          const presetRes = await runCommand('cat ' + q(presetPath), exec, 30000)
          let roleContract = ''
          if (presetRes.ok) {
            const match = /^\s*text:\s*(.+?)\s*$/m.exec(presetRes.stdout)
            if (match) {
              try { roleContract = JSON.parse(match[1]) } catch (_e) { roleContract = match[1] }
            }
          }
          const allowed = Array.isArray(envelope.allowed_output_filenames)
            ? envelope.allowed_output_filenames
            : []
          const scratch = envelope.scratch_directory || ''
          const schemaId = envelope.proposal_schema_id || ''
          const dispatchPrompt = [
            '你是 BriefLoop 的 ' + roleId + ' 角色,在 DeepSeek Harness 会话中被派发执行一次精确的角色调用。只写允许的提案文件并跑一次校验命令,然后回报。绝不触碰 SQLite/Store,绝不写 scratch 目录之外的任何文件。',
            '',
            '绑定事实(来自 RoleTaskEnvelope):',
            '- workspace 绝对路径: ' + ws,
            '- invocation_id: ' + (envelope.invocation_id || ''),
            '- scratch_directory(相对 workspace): ' + scratch,
            '- role_id: ' + roleId,
            '- 唯一允许产出: ' + JSON.stringify(allowed),
            '- proposal_schema_id: ' + schemaId,
            '- envelope 文件: ' + ws + '/' + scratch + '/role_task_envelope.json',
            '',
            '角色契约:',
            roleContract,
            '',
            '任务说明: ' + (envelope.task_instructions || ''),
            '',
            '执行:',
            '1. 用你的工具在 scratch 目录写允许的提案文件。',
            '2. 若 task_instructions 要求 preflight,运行: briefloop contract show ' + schemaId + ' --example full 与 briefloop runtime invocation-validate --workspace ' + ws + ' --envelope ' + scratch + '/role_task_envelope.json',
            '3. 回报:产出文件绝对路径、校验命令 stdout(尤其 status)、提案概要。',
            '只做这一件事,不 apply、不 accept、不碰 briefloop.db。',
          ].join('\n')
          return {
            ok: true,
            role_id: roleId,
            preset_id: presetId,
            invocation_id: envelope.invocation_id || '',
            scratch_directory: scratch,
            allowed_output_filenames: allowed,
            proposal_schema_id: schemaId,
            stage_id: envelope.stage_id || '',
            dispatch_instruction: envelope.dispatch_instruction || '',
            task_instructions: envelope.task_instructions || '',
            role_contract: roleContract,
            dispatch_prompt: dispatchPrompt,
          }
        },
      })),
    ]
    return () => {
      for (const dispose of disposers) dispose()
    }
  },
}
