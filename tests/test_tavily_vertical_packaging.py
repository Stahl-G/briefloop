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


ROOT = Path(__file__).parents[1]


def test_tavily_vertical_real_loopback_source_and_wheel_parity(
    tmp_path: Path,
) -> None:
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
        from contextlib import redirect_stdout
        import hashlib
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import http.client
        import io
        import json
        import os
        from pathlib import Path
        from threading import Thread
        import time
        from types import SimpleNamespace
        from urllib.parse import parse_qs, urlsplit
        import sys
        import webbrowser

        import multi_agent_brief
        from multi_agent_brief.cli.init_commands import _init_web_wizard
        from multi_agent_brief.cli.main import main
        from multi_agent_brief.control_store import SQLiteControlStore
        from multi_agent_brief.product import init_web as init_web_package
        from multi_agent_brief.product.init_web.server import SESSION_TOKEN_HEADER
        from multi_agent_brief.product.init_web.server import create_init_web_server
        from multi_agent_brief.product.init_web.submit import InitWebSubmitter
        from multi_agent_brief.product.projection_platform import (
            supports_retained_directory_publication,
        )
        from multi_agent_brief.sources.search_backends import tavily as tavily_module
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
        request_id = "REQ-WHEEL-TAVILY-VERTICAL-001"
        target_name = "tavily-vertical-workspace"
        provider_requests = []
        provider_authorizations = []
        response_bytes = (
            b'{"results":[{"title":"Durable public result",'
            b'"url":"https://openai.com/public-durable",'
            b'"content":"discovery summary",'
            b'"raw_content":"provider-returned durable content",'
            b'"published_date":" 2026-07-23",'
            b'"score":0.9},'
            b'{"title":"Snippet-only result",'
            b'"url":"https://openai.com/public-snippet",'
            b'"content":"snippet only","raw_content":"",'
            b'"published_date":"Wed, 22 Jul 2026 05:30:00 GMT",'
            b'"score":0.7}]}'
        )

        class TavilyHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                provider_requests.append(json.loads(self.rfile.read(length)))
                provider_authorizations.append(
                    self.headers.get("Authorization")
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response_bytes)))
                self.end_headers()
                self.wfile.write(response_bytes)

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

        def public_body(session_id):
            return {
                "schema_version": "briefloop.init_web.submission.v1",
                "request_id": request_id,
                "payload": {
                    "workspace_target": target_name,
                    "selections": {
                        "company": "Wheel ExampleCo",
                        "industry_or_theme": "manufacturing",
                        "task_objective": (
                            "Prepare the weekly manufacturing brief."
                        ),
                        "brief_title": "Wheel discovery brief",
                        "audience": "management",
                        "interface_language": "en",
                        "output_language": "en",
                        "cadence": "weekly",
                        "max_source_age_days": 30,
                        "focus_areas": ["operations"],
                        "output_formats": ["markdown"],
                        "forbidden_sources": [],
                        "source_profile": "llm_decide",
                        "web_search_mode": "external_api",
                        "search_backend": "tavily",
                        "search_domains": ["openai.com"],
                        "output_extent": "balanced",
                    },
                    "completion_target": "finalized_local",
                    "repair_budget": 1,
                    "search_secret_session_id": session_id,
                    "human_confirmation": True,
                },
            }

        provider_server = ThreadingHTTPServer(
            ("127.0.0.1", 0), TavilyHandler
        )
        provider_thread = Thread(
            target=provider_server.serve_forever, daemon=True
        )
        provider_thread.start()
        tavily_module.TAVILY_API_URL = (
            f"http://127.0.0.1:{provider_server.server_port}/search"
        )

        original_urlopen = tavily_module.urllib.request.urlopen
        secret_hash_text = hashlib.sha256(
            sentinel.encode("utf-8")
        ).hexdigest()
        echo_rejections = 0
        for echoed in (sentinel, secret_hash_text.upper()):
            echo_calls = []

            class EchoResponse:
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def read(self, _limit=-1):
                    return json.dumps(
                        {
                            "ignored_diagnostic": echoed,
                            "results": [
                                {
                                    "title": "Durable result",
                                    "url": "https://example.com/durable",
                                    "content": "search snippet",
                                    "raw_content": "durable page extract",
                                    "score": 0.9,
                                }
                            ],
                        }
                    ).encode("utf-8")

            def echo_urlopen(_request, timeout=30):
                require(timeout == 30, "echo timeout mismatch")
                echo_calls.append("called")
                return EchoResponse()

            os.environ["TAVILY_API_KEY"] = sentinel
            tavily_module.urllib.request.urlopen = echo_urlopen
            try:
                tavily_module.TavilyBackend().search_response(
                    "echo test",
                    max_results=1,
                )
            except tavily_module.SearchBackendError as exc:
                require(str(exc) == "Tavily search failed", "echo error changed")
                require(exc.__cause__ is None, "echo error cause leaked")
                require(exc.__context__ is None, "echo error context leaked")
                require(sentinel not in repr(exc), "echo secret leaked")
                require(
                    secret_hash_text not in repr(exc).lower(),
                    "echo hash leaked",
                )
                echo_rejections += 1
            else:
                raise RuntimeError("credential echo was accepted")
            finally:
                tavily_module.urllib.request.urlopen = original_urlopen
                os.environ.pop("TAVILY_API_KEY", None)
            require(len(echo_calls) == 1, "echo call count mismatch")

        first = create_init_web_server(InitWebSubmitter(base_dir=root))
        wizard_errors = []
        submit_responses = []

        def submit_via_http():
            try:
                token, session_id = credentials(first.url)
                for _attempt in range(50):
                    try:
                        status, raw = post_json(
                            first,
                            token,
                            session_id,
                            "/api/v1/search-secret",
                            {"provider": "tavily", "api_key": sentinel},
                        )
                    except OSError:
                        time.sleep(0.01)
                    else:
                        break
                else:
                    raise RuntimeError("init-web server did not accept loopback")
                require(status == 200, "search-secret failed")
                require(
                    sentinel.encode("utf-8") not in raw,
                    "secret echoed by search endpoint",
                )
                status, raw = post_json(
                    first,
                    token,
                    session_id,
                    "/api/v1/submit",
                    public_body(session_id),
                )
                require(status == 200, "submit failed")
                submit_responses.append(raw)
            except Exception as exc:
                wizard_errors.append(exc)

        init_web_package.create_init_web_server = (
            lambda *_args, **_kwargs: first
        )
        webbrowser.open = lambda _url: True
        client = Thread(target=submit_via_http, daemon=True)
        client.start()
        wizard_stream = io.StringIO()
        with redirect_stdout(wizard_stream):
            wizard_rc = _init_web_wizard(SimpleNamespace(port=0))
        client.join(timeout=2)
        require(wizard_rc == 0, "init-web wizard failed")
        require(not client.is_alive(), "init-web client did not finish")
        require(not wizard_errors, f"init-web client error: {wizard_errors!r}")

        workspace = root / target_name
        handoff = f"briefloop runtime continue --workspace {workspace}"
        require(handoff in wizard_stream.getvalue(), "runtime handoff missing")
        require(len(submit_responses) == 1, "unexpected submit response count")
        initial = json.loads(submit_responses[0])
        require(initial["execution_authorized"] is False, "execution relabeled")
        require(
            initial["source_discovery_authorized"] is True,
            "discovery authorization missing",
        )
        require(initial["search_secret_status"] == "ready", "secret not ready")
        db_path = workspace / "briefloop.db"
        env_path = workspace / ".env"
        require(db_path.is_file() and env_path.is_file(), "init files missing")
        require(env_path.read_text(encoding="utf-8").split("=", 1)[1].strip() == sentinel,
                "local credential mismatch")
        with SQLiteControlStore.open(db_path) as store:
            head = store.load_workspace_run_head()
            require(head is not None, "workspace head missing")
            initial_snapshot = store.load_snapshot(head.current_run_id)
        require(
            len(initial_snapshot.run_source_discovery_authorizations) == 1,
            "discovery authorization count mismatch",
        )
        require(
            len(initial_snapshot.run_execution_authorizations) == 0,
            "premature execution authorization",
        )

        def run_cli():
            stream = io.StringIO()
            with redirect_stdout(stream):
                rc = main(handoff.removeprefix("briefloop ").split())
            require(rc == 0, f"runtime continue failed: {stream.getvalue()!r}")
            return json.loads(stream.getvalue())

        planner = run_cli()
        require(planner["status"] == "role_work_required", "planner not required")
        require(
            planner["current_stage"] == "source-discovery",
            "planner stage mismatch",
        )
        require(provider_requests == [], "provider called before planner")
        with SQLiteControlStore.open(db_path) as store:
            head = store.load_workspace_run_head()
            require(head is not None, "workspace head missing after planner")
            planner_snapshot = store.load_snapshot(head.current_run_id)
        planners = [
            item
            for item in planner_snapshot.invocations
            if item.role_id == "source-planner" and item.status == "active"
        ]
        require(len(planners) == 1, "planner invocation count mismatch")
        planner_scratch = workspace / "scratch" / planners[0].invocation_id
        (planner_scratch / "source_candidates.yaml").write_text(
            "version: 1\ncandidates:\n  - route: web-search\n",
            encoding="utf-8",
        )

        before_acquisition = db_path.read_bytes()
        continuation = run_cli()
        capable = supports_retained_directory_publication()
        if not capable:
            require(
                continuation["status"] == "needs_attention",
                "unsupported platform did not stop",
            )
            require(
                continuation["reason_code"]
                == "checkout_publication_unsupported",
                "unsupported platform reason mismatch",
            )
            require(provider_requests == [], "provider called on unsupported platform")
            require(
                db_path.read_bytes() == before_acquisition,
                "unsupported platform changed Store",
            )
            with SQLiteControlStore.open(db_path) as store:
                head = store.load_workspace_run_head()
                require(head is not None, "unsupported workspace head missing")
                stopped_snapshot = store.load_snapshot(head.current_run_id)
            require(stopped_snapshot.sources == (), "unsupported source persisted")
            require(
                len(stopped_snapshot.run_execution_authorizations) == 0,
                "unsupported execution authorization persisted",
            )
            provider_server.shutdown()
            provider_thread.join(timeout=2)
            provider_server.server_close()
            print(
                json.dumps(
                    {
                        "capable": False,
                        "credential_echo_rejections": echo_rejections,
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
        require(len(provider_requests) == 1, "provider call count mismatch")
        provider_request = provider_requests[0]
        require(
            provider_request["query"]
            == "Prepare the weekly manufacturing brief.",
            "query mismatch",
        )
        require(provider_request["max_results"] == 5, "max_results mismatch")
        require(
            provider_request["include_raw_content"] == "markdown",
            "raw content not requested",
        )
        require(
            provider_request["include_answer"] is False,
            "answer unexpectedly requested",
        )
        require(
            provider_request["auto_parameters"] is False,
            "auto parameters unexpectedly enabled",
        )
        require(
            provider_request["search_depth"] == "basic",
            "search depth mismatch",
        )
        require(provider_request["days"] == 30, "day range mismatch")
        require("time_range" not in provider_request, "week filter used")
        require(
            provider_request["include_domains"] == ["openai.com"],
            "provider domain binding mismatch",
        )
        require("api_key" not in provider_request, "provider key entered body")
        require(
            provider_authorizations == [f"Bearer {sentinel}"],
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
            source_payloads = [
                (
                    store.read_artifact_revision_bytes(
                        head.current_run_id,
                        source.raw_payload_artifact_id,
                        source.raw_payload_artifact_revision,
                    ),
                    store.read_artifact_revision_bytes(
                        head.current_run_id,
                        source.content_artifact_id,
                        source.content_artifact_revision,
                    ),
                )
                for source in promoted.sources
            ]
        require(provider_bytes == response_bytes, "provider bytes changed")
        require(
            len(promoted.run_execution_authorizations) == 1,
            "execution authorization missing",
        )
        require(len(promoted.sources) == 2, "source count mismatch")
        published_dates = sorted(
            source.published_at
            for source in promoted.sources
            if source.published_at is not None
        )
        require(
            published_dates == ["2026-07-22"],
            "normalized published dates missing",
        )
        require(
            sorted(source.claims_eligible for source in promoted.sources)
            == [False, True],
            "source eligibility mismatch",
        )
        require(len(promotion.source_ids) == 2, "receipt source binding mismatch")
        require(
            len(promotion.run_execution_authorizations) == 1,
            "receipt execution authorization missing",
        )
        require(
            len(promotion.run_source_discovery_authorizations) == 1,
            "receipt discovery authorization missing",
        )
        require(
            any(
                content == b"provider-returned durable content"
                for _projection, content in source_payloads
            ),
            "durable provider content missing",
        )
        require(
            all(projection.startswith(b"{") for projection, _ in source_payloads),
            "raw projections missing",
        )
        raw_published_dates = sorted(
            json.loads(projection)["published_date"]
            for projection, _content in source_payloads
        )
        require(
            raw_published_dates
            == [
                " 2026-07-23",
                "Wed, 22 Jul 2026 05:30:00 GMT",
            ],
            "provider published-date projection changed",
        )
        db_bytes = db_path.read_bytes()

        provider_server.shutdown()
        provider_thread.join(timeout=2)
        provider_server.server_close()

        def no_reopen(*_args, **_kwargs):
            raise RuntimeError("committed replay reopened provider")

        WebSearchProvider.collect_with_response = no_reopen
        replayed = run_cli()
        require(
            replayed["status"] == "role_work_required",
            "committed replay lost role handoff",
        )
        require(len(provider_requests) == 1, "replay called provider")
        require(db_path.read_bytes() == db_bytes, "replay changed Store")

        secret_bytes = sentinel.encode("utf-8")
        secret_hash = hashlib.sha256(secret_bytes).hexdigest().encode("ascii")
        for path in workspace.rglob("*"):
            if path.is_file() and path != env_path:
                payload = path.read_bytes()
                require(secret_bytes not in payload, f"secret leaked to {path.name}")
                require(secret_hash not in payload, f"secret hash leaked to {path.name}")

        print(
            json.dumps(
                {
                    "capable": True,
                    "credential_echo_rejections": echo_rejections,
                    "domains": provider_request["include_domains"],
                    "durable_sources": sum(
                        source.claims_eligible for source in promoted.sources
                    ),
                    "optimize": sys.flags.optimize,
                    "provider_calls": len(provider_requests),
                    "published_dates": published_dates,
                    "raw_published_dates": raw_published_dates,
                    "role": continuation["current_stage"],
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
            "credential_echo_rejections": 2,
            "domains": ["openai.com"],
            "durable_sources": 1,
            "optimize": sys.flags.optimize,
            "provider_calls": 1,
            "published_dates": ["2026-07-22"],
            "raw_published_dates": [
                " 2026-07-23",
                "Wed, 22 Jul 2026 05:30:00 GMT",
            ],
            "role": "scout",
            "sources": 2,
            "status": "role_work_required",
        }
    else:
        expected = {
            "capable": False,
            "credential_echo_rejections": 2,
            "optimize": sys.flags.optimize,
            "provider_calls": 0,
            "reason_code": "checkout_publication_unsupported",
            "status": "needs_attention",
        }
    if source_payload != expected:
        raise AssertionError(f"unexpected E2E payload: {source_payload!r}")
