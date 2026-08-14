// BriefLoop DeepSeek Harness (DSH) operator plugin — client half.
//
// Experimental 0.16.0 official form. Registers a "BriefLoop" settings section
// (full-width, no popover) with a workspace + runtime form and a Start button.
// Starting calls the Host half's `briefloop_start` RPC, which runs the
// `briefloop` CLI launcher. This half never touches the ControlStore; all
// authority stays in the deterministic Python/SQLite layer.
//
// Styling uses the DSH theme tokens (--dsw-alias-*) so the form follows the
// active light/dark theme instead of hardcoded colors.

const CSS = `
.briefloop-launcher { display: flex; flex-direction: column; gap: 4px; max-width: 560px; }
.briefloop-launcher .bl-hint { margin: 0 0 12px; font-size: 13px; color: var(--dsw-alias-label-secondary); line-height: 1.5; }
.briefloop-launcher .bl-label { font-size: 12px; color: var(--dsw-alias-label-primary); margin-bottom: 4px; }
.briefloop-launcher .bl-input {
  width: 100%; padding: 7px 10px; box-sizing: border-box; margin-bottom: 12px;
  background: var(--dsw-alias-bg-layer-1); color: var(--dsw-alias-label-primary);
  border: 1px solid var(--dsw-alias-border-l1); border-radius: 6px; font-size: 13px;
}
.briefloop-launcher .bl-input:focus { outline: none; border-color: var(--dsw-alias-brand-primary); }
.briefloop-launcher .bl-start {
  align-self: flex-start; padding: 8px 16px; cursor: pointer; font-size: 13px;
  background: var(--dsw-alias-brand-primary); color: #fff; border: none; border-radius: 6px;
}
.briefloop-launcher .bl-start:disabled { opacity: 0.55; cursor: default; }
.briefloop-launcher .bl-result {
  margin: 14px 0 0; padding: 10px; max-height: 260px; overflow: auto; font-size: 11px;
  white-space: pre-wrap; word-break: break-word;
  background: var(--dsw-alias-bg-layer-1); color: var(--dsw-alias-label-primary);
  border: 1px solid var(--dsw-alias-border-l1); border-radius: 6px;
}
`

return {
  apply(ctx) {
    const slots = ctx.get('slots')
    if (slots === undefined) return
    styles.insert(CSS)

    slots.inject('settings.section', () => slots.register(
      { name: 'settings.section', id: 'briefloop-launcher', order: 10, label: 'BriefLoop' },
      () => {
        const [workspace, setWorkspace] = React.useState('')
        const [runtime, setRuntime] = React.useState('dsh')
        const [result, setResult] = React.useState('')
        const [busy, setBusy] = React.useState(false)

        return React.createElement('div', { className: 'briefloop-launcher' },
          React.createElement('p', { className: 'bl-hint' },
            '输入 workspace 路径并选择 runtime,点击「启动 briefloop」后,由 Host 侧调用 briefloop CLI launcher,结果回显在下方。'),
          React.createElement('label', { className: 'bl-label' }, 'Workspace'),
          React.createElement('input', {
            className: 'bl-input',
            value: workspace,
            placeholder: '/绝对路径/到/workspace',
            onChange: (e) => setWorkspace(e.target.value),
          }),
          React.createElement('label', { className: 'bl-label' }, 'Runtime'),
          React.createElement('select', {
            className: 'bl-input',
            value: runtime,
            onChange: (e) => setRuntime(e.target.value),
          },
            React.createElement('option', { value: 'dsh' }, 'dsh'),
            React.createElement('option', { value: 'codex' }, 'codex'),
          ),
          React.createElement('button', {
            className: 'bl-start',
            disabled: busy,
            onClick: async () => {
              setBusy(true)
              setResult('')
              try {
                const r = await host.call('briefloop_start', { workspace, runtime })
                setResult(typeof r === 'string' ? r : JSON.stringify(r, null, 2))
              } catch (e) {
                setResult('ERROR: ' + (e && e.message ? e.message : String(e)))
              }
              setBusy(false)
            },
          }, busy ? '运行中…' : '启动 briefloop'),
          result ? React.createElement('pre', { className: 'bl-result' }, result) : null,
        )
      },
    ))
  },
}
