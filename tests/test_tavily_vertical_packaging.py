"""Source-clone and non-editable-wheel parity for the Tavily vertical."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
import zipfile

import pytest


ROOT = Path(__file__).parents[1]


@pytest.mark.explicit_e2e
@pytest.mark.timeout(900)
def test_tavily_vertical_real_loopback_source_and_wheel_parity(
    tmp_path: Path,
) -> None:
    """One product path proves bounded Search -> Extract and replay parity."""

    build_root = tmp_path / "build-root"
    build_root.mkdir()
    shutil.copy2(ROOT / "pyproject.toml", build_root / "pyproject.toml")
    shutil.copy2(ROOT / "README.md", build_root / "README.md")
    shutil.copytree(ROOT / "src", build_root / "src")
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    build = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
        ],
        cwd=build_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        raise AssertionError(build.stdout + build.stderr)
    wheel_path = next(wheel_dir.glob("briefloop-*.whl"))
    installed = tmp_path / "installed"
    installed.mkdir()
    with zipfile.ZipFile(wheel_path) as archive:
        archive.extractall(installed)

    script = textwrap.dedent(
        r"""
        import base64
        from contextlib import redirect_stdout
        import hashlib
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import http.client
        import io
        import json
        import os
        from pathlib import Path
        from threading import Thread
        from urllib.parse import parse_qs, urlsplit
        import sys

        import multi_agent_brief
        from multi_agent_brief.cli.main import main
        from multi_agent_brief.control_store import SQLiteControlStore
        from multi_agent_brief.product.init_web.server import (
            SESSION_TOKEN_HEADER,
            create_init_web_server,
        )
        from multi_agent_brief.product.init_web.submit import InitWebSubmitter
        from multi_agent_brief.product.projection_platform import (
            supports_retained_directory_publication,
        )
        from multi_agent_brief.sources.search_backends import tavily as tavily_module
        from multi_agent_brief.sources.tavily_acquisition import (
            parse_tavily_acquisition_bundle,
        )
        from multi_agent_brief.sources.web_search import WebSearchProvider

        root = Path(sys.argv[1])
        expected_package_root = Path(sys.argv[2]).resolve()
        package_file = Path(multi_agent_brief.__file__).resolve()

        def require(condition, message):
            if not condition:
                raise RuntimeError(message)

        require(
            package_file.is_relative_to(expected_package_root),
            "package root mismatch",
        )
        require(sys.flags.optimize == int(sys.argv[3]), "optimize mismatch")
        root.mkdir()

        sentinel = "tvly-wheel-loopback-sentinel"
        target_name = "tavily-vertical-workspace"
        provider_requests = []
        provider_authorizations = []
        search_response_bytes = json.dumps(
            {
                "results": [
                    {
                        "title": "Durable public result",
                        "url": "https://openai.com/public-durable",
                        "content": "discovery summary",
                        "raw_content": "search bytes are not evidence",
                        "published_date": "2026-07-23",
                        "score": 0.9,
                    },
                    {
                        "title": "Unavailable extract result",
                        "url": "https://openai.com/public-failed",
                        "content": "second discovery summary",
                        "published_date": "2026-07-22",
                        "score": 0.7,
                    },
                ]
            },
            separators=(",", ":"),
        ).encode("utf-8")
        empty_extract_response = json.dumps(
            {
                "results": [
                    {
                        "url": "https://openai.com/public-durable",
                        "raw_content": "",
                    }
                ],
                "failed_results": [
                    {
                        "url": "https://openai.com/public-failed",
                        "error": "unavailable",
                    }
                ],
            },
            separators=(",", ":"),
        ).encode("utf-8")
        remaining_response_budget = (
            tavily_module.TAVILY_RESPONSE_BYTE_BUDGET - len(search_response_bytes)
        )
        durable_content = "x" * (
            remaining_response_budget - len(empty_extract_response) - 128
        )
        extract_response_bytes = json.dumps(
            {
                "results": [
                    {
                        "url": "https://openai.com/public-durable",
                        "raw_content": durable_content,
                    }
                ],
                "failed_results": [
                    {
                        "url": "https://openai.com/public-failed",
                        "error": "unavailable",
                    }
                ],
            },
            separators=(",", ":"),
        ).encode("utf-8")
        require(
            0 < remaining_response_budget - len(extract_response_bytes) < 256,
            "Extract response is not near the shared response budget",
        )

        class TavilyHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                body = json.loads(self.rfile.read(length))
                provider_requests.append((self.path, body))
                provider_authorizations.append(self.headers.get("Authorization"))
                if self.path == "/search":
                    payload = search_response_bytes
                elif self.path == "/extract":
                    payload = extract_response_bytes
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, _format, *_args):
                return

        def credentials(url):
            fragment = parse_qs(urlsplit(url).fragment)
            return fragment["token"][0], fragment["session"][0]

        def post_json(server, token, session_id, path, body):
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.port, timeout=5
            )
            try:
                connection.request(
                    "POST",
                    f"{path}?session_id={session_id}",
                    body=json.dumps(body).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        SESSION_TOKEN_HEADER: token,
                    },
                )
                response = connection.getresponse()
                return response.status, response.read()
            finally:
                connection.close()

        def run_cli(workspace):
            stream = io.StringIO()
            with redirect_stdout(stream):
                rc = main(["runtime", "continue", "--workspace", str(workspace)])
            require(rc == 0, f"runtime continue failed: {stream.getvalue()!r}")
            return json.loads(stream.getvalue())

        provider_server = ThreadingHTTPServer(("127.0.0.1", 0), TavilyHandler)
        provider_thread = Thread(target=provider_server.serve_forever, daemon=True)
        provider_thread.start()
        tavily_module.TAVILY_API_URL = (
            f"http://127.0.0.1:{provider_server.server_port}/search"
        )
        tavily_module.TAVILY_EXTRACT_API_URL = (
            f"http://127.0.0.1:{provider_server.server_port}/extract"
        )

        server = create_init_web_server(
            InitWebSubmitter(base_dir=root), exit_on_success=False
        )
        server.start()
        token, session_id = credentials(server.url)
        try:
            status, raw = post_json(
                server,
                token,
                session_id,
                "/api/v1/search-secret",
                {"provider": "tavily", "api_key": sentinel},
            )
            require(status == 200, f"search-secret failed: {raw!r}")
            submission = {
                "schema_version": "briefloop.init_web.submission.v1",
                "request_id": "REQ-WHEEL-TAVILY-VERTICAL-001",
                "payload": {
                    "workspace_target": target_name,
                    "selections": {
                        "company": "Wheel ExampleCo",
                        "industry_or_theme": "grid-scale energy storage",
                        "task_objective": (
                            "Prepare a public evidence brief about grid-scale "
                            "energy storage developments."
                        ),
                        "brief_title": "Grid storage developments",
                        "audience": "management",
                        "interface_language": "en",
                        "output_language": "en",
                        "cadence": "weekly",
                        "max_source_age_days": 30,
                        "focus_areas": ["Industry weekly"],
                        "output_formats": ["markdown"],
                        "forbidden_sources": [],
                        "source_profile": "llm_decide",
                        "web_search_mode": "external_api",
                        "search_backend": "tavily",
                        "search_domains": ["openai.com"],
                        "output_extent": "balanced",
                    },
                    "raw_free_text": "grid-scale energy storage",
                    "discarded": [],
                    "human_confirmation": True,
                    "completion_target": "finalized_local",
                    "repair_budget": 1,
                    "search_secret_session_id": session_id,
                },
            }
            status, raw = post_json(
                server, token, session_id, "/api/v1/submit", submission
            )
            require(status == 200, f"Init Web submit failed: {raw!r}")
            initial = json.loads(raw)
            require(initial["status"] == "committed", "initialization not committed")
            require(
                initial["source_discovery_authorized"] is True,
                "discovery authorization missing",
            )
            require(initial["search_secret_status"] == "ready", "secret not ready")
        finally:
            server.close()

        workspace = root / target_name
        db_path = workspace / "briefloop.db"
        env_path = workspace / ".env"
        require(db_path.is_file() and env_path.is_file(), "init files missing")
        require(
            env_path.read_text(encoding="utf-8").split("=", 1)[1].strip()
            == sentinel,
            "local credential mismatch",
        )

        planner = run_cli(workspace)
        require(planner["status"] == "role_work_required", "planner not required")
        require(
            planner["current_stage"] == "source-discovery",
            "planner stage mismatch",
        )
        require(provider_requests == [], "provider called before planner")
        with SQLiteControlStore.open(db_path) as store:
            head = store.load_workspace_run_head()
            require(head is not None, "workspace head missing")
            snapshot = store.load_snapshot(head.current_run_id)
        planners = [
            item
            for item in snapshot.invocations
            if item.role_id == "source-planner" and item.status == "active"
        ]
        require(len(planners) == 1, "planner invocation count mismatch")
        planner_scratch = workspace / "scratch" / planners[0].invocation_id
        (planner_scratch / "source_candidates.yaml").write_text(
            "version: 1\ncandidates:\n  - route: web-search\n",
            encoding="utf-8",
        )

        before_acquisition = db_path.read_bytes()
        continuation = run_cli(workspace)
        if not supports_retained_directory_publication():
            require(
                continuation["status"] == "needs_attention",
                "unsupported platform did not stop",
            )
            require(
                continuation["reason_code"] == "checkout_publication_unsupported",
                "unsupported platform reason mismatch",
            )
            require(provider_requests == [], "unsupported platform called provider")
            require(
                db_path.read_bytes() == before_acquisition,
                "unsupported platform changed Store",
            )
            provider_server.shutdown()
            provider_thread.join(timeout=2)
            provider_server.server_close()
            print(
                json.dumps(
                    {
                        "capable": False,
                        "optimize": sys.flags.optimize,
                        "provider_calls": 0,
                        "reason_code": continuation["reason_code"],
                        "status": continuation["status"],
                    },
                    sort_keys=True,
                )
            )
            raise SystemExit(0)

        require(
            continuation["status"] == "role_work_required",
            "promotion did not reach role work",
        )
        require(continuation["current_stage"] == "scout", "scout stage mismatch")
        require(
            [path for path, _body in provider_requests] == ["/search", "/extract"],
            "provider phase count/order mismatch",
        )
        search_request = provider_requests[0][1]
        extract_request = provider_requests[1][1]
        require(
            search_request
            == {
                "query": "grid-scale energy storage",
                "max_results": 5,
                "topic": "news",
                "search_depth": "basic",
                "include_answer": False,
                "include_raw_content": False,
                "auto_parameters": False,
                "time_range": "month",
                "include_domains": ["openai.com"],
            },
            "Search request mismatch",
        )
        require(
            extract_request
            == {
                "urls": [
                    "https://openai.com/public-durable",
                    "https://openai.com/public-failed",
                ],
                "query": "grid-scale energy storage",
                "chunks_per_source": 5,
                "extract_depth": "basic",
                "include_images": False,
                "include_favicon": False,
                "format": "markdown",
                "include_usage": True,
            },
            "Extract request mismatch",
        )
        require(
            provider_authorizations
            == [f"Bearer {sentinel}", f"Bearer {sentinel}"],
            "provider authorization mismatch",
        )

        with SQLiteControlStore.open(db_path) as store:
            head = store.load_workspace_run_head()
            require(head is not None, "workspace head missing after promotion")
            promoted = store.load_snapshot(head.current_run_id)
            history = store.load_history()
            promotions = [
                receipt
                for receipt in history.transactions
                if receipt.transaction_type == "source_evidence_intake"
            ]
            require(len(promotions) == 1, "promotion receipt count mismatch")
            promotion = promotions[0]
            provider_revisions = [
                item
                for item in promotion.artifact_revisions
                if item.artifact_id.startswith("ARTIFACT-PROVIDER-RESPONSE")
            ]
            require(
                len(provider_revisions) == 1,
                "provider response revision missing",
            )
            provider_bytes = store.read_artifact_revision_bytes(
                head.current_run_id,
                provider_revisions[0].artifact_id,
                provider_revisions[0].revision,
            )
            require(len(promoted.sources) == 1, "only Extract success must commit")
            source = promoted.sources[0]
            source_projection = store.read_artifact_revision_bytes(
                head.current_run_id,
                source.raw_payload_artifact_id,
                source.raw_payload_artifact_revision,
            )
            source_content = store.read_artifact_revision_bytes(
                head.current_run_id,
                source.content_artifact_id,
                source.content_artifact_revision,
            )

        observation = parse_tavily_acquisition_bundle(provider_bytes)
        require(
            len(provider_bytes)
            <= tavily_module.TAVILY_ACQUISITION_BUNDLE_BYTE_CAP,
            "canonical acquisition bundle exceeds the stage-safe cap",
        )
        require(
            observation.bundle.status == "extract_results_partial",
            "partial Extract status missing",
        )
        require(observation.result_count == 2, "Search URL count mismatch")
        require(observation.durable_content_count == 1, "durable count mismatch")
        require(
            base64.b64decode(observation.bundle.search.response_body_base64)
            == search_response_bytes,
            "exact Search response bytes missing from acquisition bundle",
        )
        require(
            [item.status for item in observation.bundle.outcomes]
            == ["succeeded", "provider_failed"],
            "per-URL Extract outcomes missing",
        )
        require(
            source_content == durable_content.encode("utf-8"),
            "Search snippet/raw bytes entered source content",
        )
        projection = json.loads(source_projection)
        require(
            projection["schema_version"]
            == "briefloop.tavily_extract_source_projection.v1",
            "source projection schema mismatch",
        )
        require(
            "raw_content" not in projection["search_result"],
            "Search raw content leaked into the eligible-source projection",
        )
        require(
            projection["extract_result"]["raw_content"] == durable_content,
            "exact Extract projection missing",
        )
        require(source.claims_eligible is True, "Extract source not eligible")
        require(
            len(promoted.run_execution_authorizations) == 1,
            "execution authorization missing",
        )
        require(len(promotion.source_ids) == 1, "receipt source count mismatch")
        require(
            len(promoted.run_source_acquisition_attempt_authorizations) == 1,
            "one Human attempt must remain one attempt",
        )
        db_bytes = db_path.read_bytes()

        provider_server.shutdown()
        provider_thread.join(timeout=2)
        provider_server.server_close()
        env_path.unlink()

        def no_reopen(*_args, **_kwargs):
            raise RuntimeError("committed replay reopened provider")

        WebSearchProvider.collect_with_response = no_reopen
        replayed = run_cli(workspace)
        require(
            replayed["status"] == "role_work_required",
            "committed replay lost role handoff",
        )
        require(len(provider_requests) == 2, "replay redialed provider")
        require(db_path.read_bytes() == db_bytes, "replay changed Store")

        secret_bytes = sentinel.encode("utf-8")
        secret_hash = hashlib.sha256(secret_bytes).hexdigest().encode("ascii")
        for path in workspace.rglob("*"):
            if path.is_file():
                payload = path.read_bytes()
                require(secret_bytes not in payload, f"secret leaked to {path.name}")
                require(secret_hash not in payload, f"secret hash leaked to {path.name}")

        print(
            json.dumps(
                {
                    "capable": True,
                    "durable_sources": observation.durable_content_count,
                    "optimize": sys.flags.optimize,
                    "provider_calls": len(provider_requests),
                    "provider_phases": [path for path, _body in provider_requests],
                    "role": continuation["current_stage"],
                    "search_urls": observation.result_count,
                    "sources": len(promoted.sources),
                    "status": continuation["status"],
                },
                sort_keys=True,
            )
        )
        """
    )
    script_path = tmp_path / "tavily_vertical_e2e.py"
    script_path.write_text(script, encoding="utf-8")

    def execute(label: str, package_root: Path) -> dict[str, object]:
        environment = dict(os.environ)
        environment.pop("TAVILY_API_KEY", None)
        environment["PYTHONPATH"] = str(package_root)
        optimization_flag = (
            "-" + ("O" * sys.flags.optimize) if sys.flags.optimize else None
        )
        command = [sys.executable]
        if optimization_flag is not None:
            command.append(optimization_flag)
        command.extend(
            [
                str(script_path),
                str(tmp_path / f"{label}-run"),
                str(package_root),
                str(sys.flags.optimize),
            ]
        )
        run = subprocess.run(
            command,
            cwd=tmp_path,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=90,
        )
        if run.returncode != 0:
            raise AssertionError(run.stdout + run.stderr)
        return json.loads(run.stdout)

    source_payload = execute("source", ROOT / "src")
    wheel_payload = execute("wheel", installed)
    if source_payload != wheel_payload:
        raise AssertionError(
            f"source/wheel payload mismatch: {source_payload!r} != {wheel_payload!r}"
        )
    if source_payload["optimize"] != sys.flags.optimize:
        raise AssertionError("child optimization level did not match parent")
    if source_payload["capable"]:
        expected = {
            "capable": True,
            "durable_sources": 1,
            "optimize": sys.flags.optimize,
            "provider_calls": 2,
            "provider_phases": ["/search", "/extract"],
            "role": "scout",
            "search_urls": 2,
            "sources": 1,
            "status": "role_work_required",
        }
    else:
        expected = {
            "capable": False,
            "optimize": sys.flags.optimize,
            "provider_calls": 0,
            "reason_code": "checkout_publication_unsupported",
            "status": "needs_attention",
        }
    if source_payload != expected:
        raise AssertionError(f"unexpected E2E payload: {source_payload!r}")
