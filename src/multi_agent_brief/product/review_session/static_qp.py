"""Deterministic no-JavaScript static Quality Panel renderer."""

from __future__ import annotations

from html import escape

from .contracts import PostFinalReviewReadModel


STATIC_QP_BOUNDARY = (
    "Deterministic read-only projection. No commands, provider calls, runtime "
    "effects, quality score, or delivery authority."
)


def render_static_quality_panel(read_model: PostFinalReviewReadModel) -> bytes:
    """Render a self-contained named snapshot without interactive controls."""

    quality = read_model.quality
    metrics = "".join(
        "<li><span>{}</span><strong>{}</strong><small>{}</small></li>".format(
            escape(metric.label), metric.value, escape(metric.status)
        )
        for metric in quality.metrics
    )
    sections = "".join(
        "<section><h2>{}</h2><dl>{}</dl></section>".format(
            escape(section.title),
            "".join(
                "<div><dt>{}</dt><dd><b>{}</b> {}</dd></div>".format(
                    escape(item.label),
                    escape(item.status),
                    escape(item.detail),
                )
                for item in section.items
            ),
        )
        for section in quality.sections
    )
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="briefloop-boundary" content="static-quality-panel-read-only">
<title>BriefLoop static Quality Panel</title>
<style>
:root{{--paper:#faf9f6;--surface:#fff;--ink:#1e2320;--muted:#6a706b;--line:#dedfdc;--green:#2c7a4b;--red:#c2401f;--amber:#a8540a;--gray:#6a706b}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:960px;margin:auto;padding:32px 22px 64px}}header,section,li{{background:var(--surface);border:1px solid var(--line);border-radius:10px}}header,section{{padding:20px;margin-bottom:14px}}h1{{margin:0 0 8px}}h2{{font-size:17px}}p,small{{color:var(--muted)}}code{{overflow-wrap:anywhere}}ul{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;padding:0;list-style:none}}li{{padding:12px;display:grid;gap:4px}}li strong{{font-size:22px}}dl div{{display:grid;grid-template-columns:minmax(160px,1fr) 2fr;gap:14px;padding:9px 0;border-top:1px solid var(--line)}}dd{{margin:0}}footer{{color:var(--muted);font-size:12px}}
</style></head><body><main>
<header><p>Audit attachment · read-only</p><h1>BriefLoop Quality Panel</h1>
<p>{escape(STATIC_QP_BOUNDARY)}</p>
<p>Run <code>{escape(read_model.context.run_id)}</code> · Report <code>{escape(read_model.context.report_sha256)}</code></p>
<strong>Overall status: {escape(quality.overall_status)}</strong></header>
<ul>{metrics}</ul>{sections}
<footer>Projection fingerprint: <code>{escape(quality.projection_fingerprint)}</code> · No JavaScript or command endpoint.</footer>
</main></body></html>"""
    return html.encode("utf-8")


__all__ = ["STATIC_QP_BOUNDARY", "render_static_quality_panel"]
