// BriefLoop DeepSeek Harness (DSH) operator plugin — client half.
//
// Experimental 0.16.0 official form. Adds a "BriefLoop" action at the sidebar
// foot that opens a small form (workspace path + runtime) and starts the
// workspace through the Host half's `briefloop_start` RPC, which runs the
// `briefloop` CLI launcher. This half never touches the ControlStore; all
// authority stays in the deterministic Python/SQLite layer.

return {
  apply(ctx) {
    const slots = ctx.get('slots')
    if (slots === undefined) return

    slots.inject('sidebar.footer.action', () => slots.register(
      { name: 'sidebar.footer.action', id: 'briefloop-launcher', order: 10, label: 'BriefLoop' },
      () => {
        const [open, setOpen] = React.useState(false)
        const [workspace, setWorkspace] = React.useState('')
        const [runtime, setRuntime] = React.useState('dsh')
        const [result, setResult] = React.useState('')
        const [busy, setBusy] = React.useState(false)

        return React.createElement('div', { style: { position: 'relative' } },
          React.createElement('button', {
            title: '启动 BriefLoop',
            onClick: () => setOpen(!open),
            style: { cursor: 'pointer', background: 'transparent', border: '1px solid #555', borderRadius: '4px', color: 'inherit', padding: '4px 8px' },
          }, 'BriefLoop'),
          open ? React.createElement('div', {
            style: {
              position: 'absolute', left: 0, top: '110%', background: '#1e1e1e',
              border: '1px solid #444', borderRadius: '6px', padding: '10px',
              zIndex: 1000, minWidth: '300px', boxShadow: '0 4px 16px rgba(0,0,0,0.4)',
            },
          },
            React.createElement('label', { style: { display: 'block', fontSize: '12px', marginBottom: '2px' } }, 'Workspace'),
            React.createElement('input', {
              value: workspace,
              placeholder: '/abs/path/to/workspace',
              onChange: (e) => setWorkspace(e.target.value),
              style: { width: '100%', marginBottom: '6px', padding: '4px', boxSizing: 'border-box' },
            }),
            React.createElement('label', { style: { display: 'block', fontSize: '12px', marginBottom: '2px' } }, 'Runtime'),
            React.createElement('select', {
              value: runtime,
              onChange: (e) => setRuntime(e.target.value),
              style: { width: '100%', marginBottom: '8px', padding: '4px' },
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
              style: { width: '100%', padding: '6px', cursor: 'pointer' },
            }, busy ? '运行中…' : '启动 briefloop'),
            result ? React.createElement('pre', {
              style: { maxHeight: '220px', overflow: 'auto', fontSize: '11px', whiteSpace: 'pre-wrap', marginTop: '8px' },
            }, result) : null,
          ) : null,
        )
      },
    ))
  },
}
