// BriefLoop DeepSeek Harness (DSH) operator plugin — client half.
//
// Experimental 0.16.0 official form. Registers a "BriefLoop" settings section
// (full-width, no popover) with a workspace + runtime form and a Start button.
// Starting calls the Host half's `briefloop_start` RPC, which runs the
// `briefloop` CLI launcher. This half never touches the ControlStore; all
// authority stays in the deterministic Python/SQLite layer.

return {
  apply(ctx) {
    const slots = ctx.get('slots')
    if (slots === undefined) return

    slots.inject('settings.section', () => slots.register(
      { name: 'settings.section', id: 'briefloop-launcher', order: 10, label: 'BriefLoop' },
      () => {
        const [workspace, setWorkspace] = React.useState('')
        const [runtime, setRuntime] = React.useState('dsh')
        const [result, setResult] = React.useState('')
        const [busy, setBusy] = React.useState(false)

        const label = { display: 'block', marginBottom: '4px', fontSize: '12px' }
        const control = {
          width: '100%', padding: '6px 8px', boxSizing: 'border-box',
          marginBottom: '12px', borderRadius: '4px', border: '1px solid #444',
          background: '#111', color: 'inherit', fontSize: '13px',
        }

        return React.createElement('div', { style: { padding: '4px 0', maxWidth: '560px' } },
          React.createElement('p', {
            style: { margin: '0 0 14px', fontSize: '13px', color: '#999', lineHeight: '1.5' },
          }, '输入 workspace 路径并选择 runtime,点击「启动 briefloop」后,由 Host 侧调用 briefloop CLI launcher,结果回显在下方。'),
          React.createElement('label', { style: label }, 'Workspace'),
          React.createElement('input', {
            value: workspace,
            placeholder: '/绝对路径/到/workspace',
            onChange: (e) => setWorkspace(e.target.value),
            style: control,
          }),
          React.createElement('label', { style: label }, 'Runtime'),
          React.createElement('select', {
            value: runtime,
            onChange: (e) => setRuntime(e.target.value),
            style: control,
          },
            React.createElement('option', { value: 'dsh' }, 'dsh'),
            React.createElement('option', { value: 'codex' }, 'codex'),
          ),
          React.createElement('button', {
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
            style: {
              padding: '8px 16px', cursor: 'pointer', borderRadius: '4px',
              border: '1px solid #555', background: '#2a2a2a', color: 'inherit', fontSize: '13px',
            },
          }, busy ? '运行中…' : '启动 briefloop'),
          result ? React.createElement('pre', {
            style: {
              marginTop: '14px', padding: '8px', maxHeight: '260px', overflow: 'auto',
              fontSize: '11px', whiteSpace: 'pre-wrap', background: '#111',
              border: '1px solid #333', borderRadius: '4px',
            },
          }, result) : null,
        )
      },
    ))
  },
}
