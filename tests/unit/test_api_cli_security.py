"""Offline security & compatibility unit tests for backend API, deploy config, and CLI."""

import argparse
import importlib
import json
import logging
import os
from pathlib import Path
import sys
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from google.api_core.exceptions import NotFound
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "src" / "backend_apis"
CLI_DIR = REPO_ROOT / "src" / "cli"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

import main  # noqa: E402
from metadata_wizard_cli import cli  # noqa: E402


def _client_options_payload(**overrides):
    payload = {
        "use_lineage_tables": False,
        "use_lineage_processes": False,
        "use_profile": False,
        "use_data_quality": False,
        "use_ext_documents": False,
        "persist_to_dataplex_catalog": True,
        "stage_for_review": True,
        "top_values_in_description": True,
        "description_handling": "append",
        "description_prefix": "===AI generated description===",
        "dry_run": False,
        "confirm_overwrite": False,
    }
    payload.update(overrides)
    return payload


def _client_settings_payload(project_id="allowed-proj"):
    return {
        "project_id": project_id,
        "llm_location": "us-central1",
        "dataplex_location": "us-central1",
    }


def _table_settings_payload(
    project_id="allowed-proj", dataset_id="my_ds", table_id="my_tbl", documentation_uri=""
):
    return {
        "project_id": project_id,
        "dataset_id": dataset_id,
        "table_id": table_id,
        "documentation_uri": documentation_uri,
    }


def _dataset_settings_payload(
    project_id="allowed-proj",
    dataset_id="my_ds",
    documentation_csv_uri="",
    strategy="NAIVE",
):
    return {
        "project_id": project_id,
        "dataset_id": dataset_id,
        "documentation_csv_uri": documentation_csv_uri,
        "strategy": strategy,
    }


@pytest.fixture
def base_env(monkeypatch):
    monkeypatch.setenv("METADATA_WIZARD_API_TOKEN", "test-secret-token-123")
    monkeypatch.setenv("METADATA_WIZARD_ALLOWED_PROJECTS", "allowed-proj,second-proj")
    monkeypatch.delenv("METADATA_WIZARD_ALLOW_ANY_PROJECT", raising=False)
    monkeypatch.delenv("METADATA_WIZARD_ALLOWED_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GCP_PROJECT", raising=False)
    monkeypatch.delenv("METADATA_WIZARD_OIDC_AUDIENCE", raising=False)
    monkeypatch.delenv("METADATA_WIZARD_OIDC_ALLOWED_EMAILS", raising=False)
    monkeypatch.delenv("METADATA_WIZARD_LLM_PROJECT_ID", raising=False)
    monkeypatch.delenv("METADATA_WIZARD_ALLOW_DIRECT_BQ_WRITE", raising=False)
    monkeypatch.delenv("METADATA_WIZARD_ALLOW_REPLACE", raising=False)
    monkeypatch.setenv("METADATA_WIZARD_ALLOWED_DOC_BUCKETS", "allowed-docs-bucket")


@pytest.fixture
def test_client(base_env):
    return TestClient(main.app)


def _mock_wizard_client():
    mock = MagicMock()
    mock._project_id = "allowed-proj"
    mock._client_options = MagicMock()
    mock._cloud_clients = {"dataplex_catalog": MagicMock()}
    mock.generate_table_description.return_value = {
        "status": "dry_run",
        "dry_run": True,
        "table": "allowed-proj.my_ds.my_tbl",
        "previous_description": "Old desc",
        "proposed_description": "New desc",
    }
    mock.generate_columns_descriptions.return_value = {
        "status": "dry_run",
        "dry_run": True,
        "table": "allowed-proj.my_ds.my_tbl",
        "columns": [],
    }
    mock.generate_dataset_tables_descriptions.return_value = {
        "status": "dry_run",
        "dry_run": True,
        "dataset": "allowed-proj.my_ds",
        "tables": [],
    }
    mock.generate_dataset_tables_columns_descriptions.return_value = {
        "status": "dry_run",
        "dry_run": True,
        "dataset": "allowed-proj.my_ds",
        "tables": [],
    }
    mock.regenerate_dataset_tables_columns_descriptions.return_value = {
        "status": "dry_run",
        "dry_run": True,
        "dataset": "allowed-proj.my_ds",
        "tables": [],
    }
    mock.get_comments_to_table_draft_description.return_value = []
    mock.get_negative_examples_to_table_draft_description.return_value = []
    mock.get_review_item_details.return_value = {
        "type": "table",
        "id": "allowed-proj.my_ds.my_tbl#table",
        "name": "allowed-proj.my_ds.my_tbl",
        "currentDescription": "Old desc",
        "draftDescription": "Draft desc",
    }
    mock._dataplex_ops.update_table_draft_description.return_value = True
    mock.accept_table_draft_description.return_value = True
    mock.accept_column_draft_description.return_value = True
    mock._table_ops._list_tables_in_dataset_for_regeneration.return_value = ["allowed-proj.my_ds.my_tbl"]
    mock._review_ops.get_review_items_for_dataset.return_value = {
        "data": {"items": [], "nextPageToken": None, "totalCount": 0}
    }
    mock._review_ops.build_search_query_for_regeneration.return_value = (
        "system=BIGQUERY and aspect:global.metadata-ai-generated.to-be-regenerated=true AND parent:allowed-proj.my_ds"
    )
    mock.reject_review_item.return_value = {"success": True, "message": "ok"}
    mock.edit_review_item.return_value = {"success": True, "message": "ok"}
    mock.add_comment_to_table_draft_description.return_value = True
    mock.add_comment_to_column_draft_description.return_value = True
    mock.mark_table_for_regeneration.return_value = True
    mock.mark_column_for_regeneration.return_value = True
    return mock


class TestAuthenticationAndDeployment:
    def test_fail_closed_when_auth_not_configured(self, monkeypatch):
        monkeypatch.delenv("METADATA_WIZARD_API_TOKEN", raising=False)
        monkeypatch.delenv("METADATA_WIZARD_OIDC_AUDIENCE", raising=False)
        client = TestClient(main.app)

        with patch("main.Client") as mock_cls:
            resp = client.get("/version")
            assert resp.status_code == 503
            resp_post = client.post(
                "/metadata/review/details",
                json={
                    "client_settings": _client_settings_payload(),
                    "table_settings": _table_settings_payload(),
                },
            )
            assert resp_post.status_code == 503
            mock_cls.assert_not_called()

    def test_all_routes_reject_unauthenticated_and_bad_tokens(self, test_client):
        routes = [
            ("GET", "/version", None),
            ("POST", "/generate_table_description", {}),
            ("POST", "/generate_columns_descriptions", {}),
            ("POST", "/generate_dataset_tables_descriptions", {}),
            ("POST", "/generate_dataset_tables_columns_descriptions", {}),
            ("POST", "/accept_table_draft_description", {}),
            ("POST", "/accept_column_draft_description", {}),
            ("POST", "/get_regeneration_counts", {}),
            ("GET", "/get_regeneration_counts", None),
            ("POST", "/regenerate_selected", {}),
            ("POST", "/regenerate_all", {}),
            ("POST", "/metadata/review", {}),
            ("POST", "/metadata/review/allowed-proj.my_ds.my_tbl%23table/reject", {}),
            ("POST", "/metadata/review/allowed-proj.my_ds.my_tbl%23table/edit", {}),
            ("POST", "/metadata/review/allowed-proj.my_ds.my_tbl%23table/comment", {}),
            ("POST", "/mark_for_regeneration", {}),
            ("POST", "/metadata/review/details", {}),
            ("POST", "/update_table_draft_description", {}),
            ("POST", "/metadata/review/add_comment", {}),
            ("POST", "/metadata/review/add_negative_example", {}),
        ]
        for method, path, body in routes:
            if method == "GET":
                r_no_auth = test_client.get(path)
                r_bad_key = test_client.get(path, headers={"X-API-Key": "wrong"})
                r_bad_bearer = test_client.get(path, headers={"Authorization": "Bearer wrong"})
            else:
                r_no_auth = test_client.post(path, json=body)
                r_bad_key = test_client.post(path, json=body, headers={"X-API-Key": "wrong"})
                r_bad_bearer = test_client.post(
                    path, json=body, headers={"Authorization": "Bearer wrong"}
                )

            assert r_no_auth.status_code == 401, f"{method} {path} lacked auth"
            assert r_no_auth.headers.get("www-authenticate") == "Bearer"
            assert r_bad_key.status_code == 401
            assert r_bad_bearer.status_code == 401

    def test_accepts_x_api_key_and_bearer_token(self, test_client):
        r1 = test_client.get("/version", headers={"X-API-Key": "test-secret-token-123"})
        r2 = test_client.get(
            "/version", headers={"Authorization": "Bearer test-secret-token-123"}
        )
        assert r1.status_code == 200
        assert r2.status_code == 200
        # Invalid X-API-Key must fail even if Bearer is valid
        r3 = test_client.get(
            "/version",
            headers={
                "X-API-Key": "wrong",
                "Authorization": "Bearer test-secret-token-123",
            },
        )
        assert r3.status_code == 401

    def test_oidc_token_verification(self, monkeypatch):
        monkeypatch.delenv("METADATA_WIZARD_API_TOKEN", raising=False)
        monkeypatch.setenv("METADATA_WIZARD_OIDC_AUDIENCE", "https://metadata-wizard.run.app")
        monkeypatch.setenv("METADATA_WIZARD_OIDC_ALLOWED_EMAILS", "user@example.com")
        client = TestClient(main.app)

        with patch("main.google_id_token.verify_oauth2_token") as mock_verify:
            mock_verify.return_value = {
                "iss": "https://accounts.google.com",
                "email": "user@example.com",
                "email_verified": True,
            }
            resp = client.get("/version", headers={"Authorization": "Bearer valid-oidc-jwt"})
            assert resp.status_code == 200

            mock_verify.return_value = {
                "iss": "https://accounts.google.com",
                "email": "attacker@evil.com",
                "email_verified": True,
            }
            resp_forbidden_email = client.get(
                "/version", headers={"Authorization": "Bearer other-oidc-jwt"}
            )
            assert resp_forbidden_email.status_code == 401

    def test_non_ascii_auth_headers_return_401_not_500(self, monkeypatch):
        import anyio

        monkeypatch.setenv("METADATA_WIZARD_API_TOKEN", "test-secret-token-123")

        async def _raw_asgi_get(raw_headers):
            status_code = None
            scope = {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/version",
                "raw_path": b"/version",
                "query_string": b"",
                "headers": raw_headers,
                "client": ("127.0.0.1", 12345),
                "server": ("testserver", 80),
            }

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message):
                nonlocal status_code
                if message["type"] == "http.response.start":
                    status_code = message["status"]

            await main.app(scope, receive, send)
            return status_code

        assert (
            anyio.run(_raw_asgi_get, [(b"x-api-key", "t\xe9st".encode("latin-1"))])
            == 401
        )
        assert (
            anyio.run(
                _raw_asgi_get,
                [(b"authorization", b"Bearer " + "t\xe9st".encode("latin-1"))],
            )
            == 401
        )

    def test_openapi_and_docs_routes_disabled(self, monkeypatch):
        monkeypatch.delenv("METADATA_WIZARD_API_TOKEN", raising=False)
        monkeypatch.delenv("METADATA_WIZARD_OIDC_AUDIENCE", raising=False)
        client = TestClient(main.app)

        for doc_path in ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"):
            resp = client.get(doc_path)
            assert resp.status_code == 404, f"Expected 404 for {doc_path}, got {resp.status_code}"

    def test_cloud_run_deploy_script_and_dockerfile_hardened(self):
        deploy_script = (BACKEND_DIR / "build_deploy_cloud.sh").read_text()
        assert "--no-allow-unauthenticated" in deploy_script
        assert "--allow-unauthenticated" not in deploy_script
        assert "--service-account" in deploy_script
        assert "--set-env-vars" in deploy_script
        assert '"^;^' in deploy_script
        for required_var in (
            "METADATA_WIZARD_API_TOKEN",
            "METADATA_WIZARD_ALLOWED_PROJECTS",
            "METADATA_WIZARD_ALLOWED_DOC_BUCKETS",
            "METADATA_WIZARD_CORS_ORIGINS",
        ):
            assert required_var in deploy_script

        dockerfile = (BACKEND_DIR / "Dockerfile").read_text()
        assert "dataplexutils_metadata_wizard-0.0.2.tar.gz" in dockerfile
        assert "0.0.1.tar.gz" not in dockerfile

        cli_setup = (BACKEND_DIR.parent / "cli" / "setup.py").read_text()
        assert "google-auth>=2.0.0" in cli_setup


class TestCORSErrorSanitizationAndLogging:
    def test_cors_ignores_wildcard_and_restricts_origins(self, monkeypatch):
        monkeypatch.setenv("METADATA_WIZARD_API_TOKEN", "test-secret-token-123")
        monkeypatch.setenv(
            "METADATA_WIZARD_CORS_ORIGINS", "https://allowed.example.com,*,invalid-origin"
        )
        reloaded = importlib.reload(main)
        try:
            client = TestClient(reloaded.app)
            r_ok = client.options(
                "/version",
                headers={
                    "Origin": "https://allowed.example.com",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "X-API-Key, Content-Type",
                },
            )
            assert r_ok.headers.get("access-control-allow-origin") == "https://allowed.example.com"
            assert r_ok.headers.get("access-control-allow-credentials") is None

            r_evil = client.options(
                "/version",
                headers={
                    "Origin": "https://evil.example.com",
                    "Access-Control-Request-Method": "POST",
                },
            )
            assert "access-control-allow-origin" not in r_evil.headers
        finally:
            monkeypatch.delenv("METADATA_WIZARD_CORS_ORIGINS", raising=False)
            importlib.reload(main)

    def test_error_sanitization_and_no_secret_logging(self, test_client, caplog):
        mock_client = _mock_wizard_client()
        mock_client.get_review_item_details.side_effect = RuntimeError(
            "INTERNAL_SECRET_DUMP_DO_NOT_LEAK"
        )
        with caplog.at_level(logging.DEBUG), patch("main.Client", return_value=mock_client):
            resp = test_client.post(
                "/metadata/review/details",
                headers={"X-API-Key": "test-secret-token-123"},
                json={
                    "client_settings": _client_settings_payload(),
                    "table_settings": _table_settings_payload(),
                },
            )
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert detail.startswith("Internal server error (ref: ")
        assert "INTERNAL_SECRET_DUMP_DO_NOT_LEAK" not in detail
        assert "test-secret-token-123" not in caplog.text

    def test_exception_status_mapping_and_unbound_fix(self, test_client):
        mock_client = _mock_wizard_client()
        with patch("main.Client", return_value=mock_client):
            mock_client.get_review_item_details.side_effect = NotFound("missing table")
            r_404 = test_client.post(
                "/metadata/review/details",
                headers={"X-API-Key": "test-secret-token-123"},
                json={
                    "client_settings": _client_settings_payload(),
                    "table_settings": _table_settings_payload(),
                },
            )
            assert r_404.status_code == 404

            mock_client.get_review_item_details.side_effect = PermissionError("secret detail")
            r_403 = test_client.post(
                "/metadata/review/details",
                headers={"X-API-Key": "test-secret-token-123"},
                json={
                    "client_settings": _client_settings_payload(),
                    "table_settings": _table_settings_payload(),
                },
            )
            assert r_403.status_code == 403
            assert "secret detail" not in r_403.text

            mock_client.generate_table_description.side_effect = ValueError(
                "bad uri gs://private-bucket/secret.pdf"
            )
            r_400 = test_client.post(
                "/generate_table_description",
                headers={"X-API-Key": "test-secret-token-123"},
                json={
                    "client_options_settings": _client_options_payload(),
                    "client_settings": _client_settings_payload(),
                    "table_settings": _table_settings_payload(),
                },
            )
            assert r_400.status_code == 400
            assert "gs://private-bucket" not in r_400.text

        # Ensure Client() init error in get_review_item_details doesn't raise UnboundLocalError
        with patch("main.Client", side_effect=RuntimeError("boom")):
            r_early = test_client.post(
                "/metadata/review/details",
                headers={"X-API-Key": "test-secret-token-123"},
                json={
                    "client_settings": _client_settings_payload(),
                    "table_settings": _table_settings_payload(),
                },
            )
            assert r_early.status_code == 500
            assert "Internal server error" in r_early.json()["detail"]


class TestProjectAllowlistValidationAndPolicyGates:
    def test_fail_closed_when_project_allowlist_unset(self, monkeypatch):
        monkeypatch.setenv("METADATA_WIZARD_API_TOKEN", "test-secret-token-123")
        monkeypatch.delenv("METADATA_WIZARD_ALLOWED_PROJECTS", raising=False)
        monkeypatch.delenv("METADATA_WIZARD_ALLOWED_PROJECT", raising=False)
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.delenv("GCP_PROJECT", raising=False)
        monkeypatch.delenv("METADATA_WIZARD_ALLOW_ANY_PROJECT", raising=False)
        client = TestClient(main.app)

        with patch("main.Client") as mock_cls:
            resp = client.post(
                "/metadata/review/details",
                headers={"X-API-Key": "test-secret-token-123"},
                json={
                    "client_settings": _client_settings_payload(),
                    "table_settings": _table_settings_payload(),
                },
            )
            assert resp.status_code == 403
            mock_cls.assert_not_called()

    def test_allow_any_project_explicit_opt_in(self, monkeypatch, test_client):
        monkeypatch.delenv("METADATA_WIZARD_ALLOWED_PROJECTS", raising=False)
        monkeypatch.setenv("METADATA_WIZARD_ALLOW_ANY_PROJECT", "true")
        mock_client = _mock_wizard_client()
        with patch("main.Client", return_value=mock_client):
            resp = test_client.post(
                "/metadata/review/details",
                headers={"X-API-Key": "test-secret-token-123"},
                json={
                    "client_settings": _client_settings_payload("any-valid-proj"),
                    "table_settings": _table_settings_payload("another-valid-proj"),
                },
            )
        assert resp.status_code == 200

    def test_cross_project_rejected_across_all_endpoints(self, test_client):
        headers = {"X-API-Key": "test-secret-token-123"}
        with patch("main.Client") as mock_cls:
            # table_settings cross-project
            for path in (
                "/metadata/review/details",
                "/generate_table_description",
                "/generate_columns_descriptions",
                "/accept_table_draft_description",
            ):
                resp = test_client.post(
                    path,
                    headers=headers,
                    json={
                        "client_options_settings": _client_options_payload(),
                        "client_settings": _client_settings_payload("allowed-proj"),
                        "table_settings": _table_settings_payload("victim-proj"),
                    },
                )
                assert resp.status_code == 403, f"Expected 403 on {path}"

            # update_table_draft_description cross-project
            resp_upd = test_client.post(
                "/update_table_draft_description",
                headers=headers,
                json={
                    "client_settings": _client_settings_payload("allowed-proj"),
                    "table_settings": _table_settings_payload("victim-proj"),
                    "description": "pwn",
                    "is_html": False,
                },
            )
            assert resp_upd.status_code == 403

            # dataset_settings cross-project
            for path in (
                "/generate_dataset_tables_descriptions",
                "/generate_dataset_tables_columns_descriptions",
                "/regenerate_all",
                "/metadata/review",
                "/get_regeneration_counts",
            ):
                resp = test_client.post(
                    path,
                    headers=headers,
                    json={
                        "client_options_settings": _client_options_payload(),
                        "client_settings": _client_settings_payload("allowed-proj"),
                        "dataset_settings": _dataset_settings_payload("victim-proj"),
                    },
                )
                assert resp.status_code == 403, f"Expected 403 on {path}"

            # GET /get_regeneration_counts cross-project
            resp_get = test_client.get(
                "/get_regeneration_counts",
                headers=headers,
                params={
                    "project_id": "allowed-proj",
                    "llm_location": "us-central1",
                    "dataplex_location": "us-central1",
                    "dataset_project_id": "victim-proj",
                    "dataset_id": "ds",
                },
            )
            assert resp_get.status_code == 403

            # mark_for_regeneration cross-project table_fqn
            resp_mark = test_client.post(
                "/mark_for_regeneration",
                headers=headers,
                json={
                    "client_settings": _client_settings_payload("allowed-proj"),
                    "request": {"table_fqn": "victim-proj.ds.tbl"},
                },
            )
            assert resp_mark.status_code == 403

            # review/{id}/* cross-project embedded in path item id
            for item_id in ("victim-proj.ds.tbl%23table", "table:victim-proj.ds.tbl"):
                resp_rej = test_client.post(
                    f"/metadata/review/{item_id}/reject",
                    headers=headers,
                    json=_client_settings_payload("allowed-proj"),
                )
                assert resp_rej.status_code == 403

                resp_edit = test_client.post(
                    f"/metadata/review/{item_id}/edit",
                    headers=headers,
                    json={
                        "client_settings": _client_settings_payload("allowed-proj"),
                        "description": "new desc",
                    },
                )
                assert resp_edit.status_code == 403

                resp_cmt = test_client.post(
                    f"/metadata/review/{item_id}/comment",
                    headers=headers,
                    json={
                        "client_settings": _client_settings_payload("allowed-proj"),
                        "comment": "hello",
                    },
                )
                assert resp_cmt.status_code == 403

            mock_cls.assert_not_called()

    def test_missing_dataset_id_rejected_on_dataset_endpoints(self, test_client):
        headers = {"X-API-Key": "test-secret-token-123"}
        with patch("main.Client") as mock_cls:
            for path in (
                "/generate_dataset_tables_descriptions",
                "/generate_dataset_tables_columns_descriptions",
                "/regenerate_all",
                "/get_regeneration_counts",
            ):
                resp = test_client.post(
                    path,
                    headers=headers,
                    json={
                        "client_options_settings": _client_options_payload(),
                        "client_settings": _client_settings_payload(),
                        "dataset_settings": _dataset_settings_payload(dataset_id=""),
                    },
                )
                assert resp.status_code == 400
            mock_cls.assert_not_called()

    def test_metadata_review_allows_empty_dataset_id_without_proj_none(self, test_client):
        mock_client = _mock_wizard_client()
        with patch("main.Client", return_value=mock_client):
            resp = test_client.post(
                "/metadata/review",
                headers={"X-API-Key": "test-secret-token-123"},
                json={
                    "client_settings": _client_settings_payload(),
                    "dataset_settings": _dataset_settings_payload(dataset_id=""),
                },
            )
        assert resp.status_code == 200
        mock_client._review_ops.get_review_items_for_dataset.assert_called_once_with(
            dataset_fqn=None
        )

    def test_metadata_review_filters_out_disallowed_project_items(self, test_client):
        mock_client = _mock_wizard_client()
        mock_client._review_ops.get_review_items_for_dataset.return_value = {
            "data": {
                "items": [
                    {"id": "allowed-proj.my_ds.good_tbl#table", "name": "good_tbl"},
                    {"id": "victim-proj.my_ds.secret_tbl#table", "name": "secret_tbl"},
                    {"id": "second-proj.my_ds.ok_col#column#col_a", "name": "col_a"},
                    {"id": "#malformed"},
                ],
                "nextPageToken": "next-tok",
                "totalCount": 4,
            }
        }
        with patch("main.Client", return_value=mock_client):
            resp = test_client.post(
                "/metadata/review",
                headers={"X-API-Key": "test-secret-token-123"},
                json={
                    "client_settings": _client_settings_payload(),
                    "dataset_settings": _dataset_settings_payload(dataset_id=""),
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["totalCount"] == 2
        assert [item["id"] for item in body["items"]] == [
            "allowed-proj.my_ds.good_tbl#table",
            "second-proj.my_ds.ok_col#column#col_a",
        ]
        assert body["nextPageToken"] == "next-tok"

    def test_regenerate_selected_rejects_cross_project_objects_and_skips_mismatched_results(
        self, test_client
    ):
        headers = {"X-API-Key": "test-secret-token-123"}
        with patch("main.Client") as mock_cls:
            for objects in (["victim-proj.ds.tbl"], ["ok_tbl", "victim-proj.ds.tbl"]):
                resp = test_client.post(
                    "/regenerate_selected",
                    headers=headers,
                    json={
                        "client_options_settings": _client_options_payload(),
                        "client_settings": _client_settings_payload(),
                        "dataset_settings": _dataset_settings_payload(),
                        "regeneration_request": {"objects": objects},
                    },
                )
                assert resp.status_code == 403
            mock_cls.assert_not_called()

        mock_client = _mock_wizard_client()

        def _make_search_result(project, dataset, table):
            res = MagicMock()
            entry = MagicMock()
            entry.name = f"projects/{project}/locations/us/entryGroups/@bigquery/entries/{table}"
            entry.fully_qualified_name = f"bigquery:{project}.{dataset}.{table}"
            entry.entry_source.resource = (
                f"//bigquery.googleapis.com/projects/{project}/datasets/{dataset}/tables/{table}"
            )
            entry.entry_source.display_name = table
            res.dataplex_entry = entry
            return res

        mock_client._cloud_clients["dataplex_catalog"].search_entries.return_value = [
            _make_search_result("allowed-proj", "my_ds", "good_tbl"),
            _make_search_result("victim-proj", "my_ds", "cross_proj_tbl"),
            _make_search_result("allowed-proj", "other_ds", "wrong_ds_tbl"),
        ]
        with patch("main.Client", return_value=mock_client):
            resp_ok = test_client.post(
                "/regenerate_selected",
                headers=headers,
                json={
                    "client_options_settings": _client_options_payload(),
                    "client_settings": _client_settings_payload(),
                    "dataset_settings": _dataset_settings_payload(),
                    "regeneration_request": {"objects": ["allowed-proj.my_ds.good_tbl"]},
                },
            )
        assert resp_ok.status_code == 200
        mock_client.generate_table_description.assert_called_once_with(
            "allowed-proj.my_ds.good_tbl"
        )

    def test_policy_gates_block_direct_bq_write_and_replace_by_default(self, monkeypatch, test_client):
        headers = {"X-API-Key": "test-secret-token-123"}
        with patch("main.Client") as mock_cls:
            # stage_for_review=False, dry_run=False -> 403
            r_direct = test_client.post(
                "/generate_table_description",
                headers=headers,
                json={
                    "client_options_settings": _client_options_payload(stage_for_review=False),
                    "client_settings": _client_settings_payload(),
                    "table_settings": _table_settings_payload(),
                },
            )
            assert r_direct.status_code == 403

            # description_handling="replace" -> 403
            r_replace = test_client.post(
                "/generate_columns_descriptions",
                headers=headers,
                json={
                    "client_options_settings": _client_options_payload(
                        stage_for_review=True, description_handling="replace"
                    ),
                    "client_settings": _client_settings_payload(),
                    "table_settings": _table_settings_payload(),
                },
            )
            assert r_replace.status_code == 403
            mock_cls.assert_not_called()

        # dry_run=True is allowed even when stage_for_review=False
        mock_client = _mock_wizard_client()
        with patch("main.Client", return_value=mock_client):
            r_dry = test_client.post(
                "/generate_table_description",
                headers=headers,
                json={
                    "client_options_settings": _client_options_payload(
                        stage_for_review=False, dry_run=True
                    ),
                    "client_settings": _client_settings_payload(),
                    "table_settings": _table_settings_payload(),
                },
            )
            assert r_dry.status_code == 200
            body = r_dry.json()
            assert body["dry_run"] is True
            assert body["preview"]["previous_description"] == "Old desc"

    def test_accept_draft_enforces_replace_policy_and_orders_bq_before_dataplex(
        self, monkeypatch, test_client
    ):
        headers = {"X-API-Key": "test-secret-token-123"}
        with patch("main.Client") as mock_cls:
            for handling in ("replace", "REPLACE"):
                r_tbl = test_client.post(
                    "/accept_table_draft_description",
                    headers=headers,
                    json={
                        "client_options_settings": _client_options_payload(
                            description_handling=handling,
                            confirm_overwrite=True,
                        ),
                        "client_settings": _client_settings_payload(),
                        "table_settings": _table_settings_payload(),
                    },
                )
                assert r_tbl.status_code == 403

                r_col = test_client.post(
                    "/accept_column_draft_description",
                    headers=headers,
                    json={
                        "client_options_settings": _client_options_payload(
                            description_handling=handling,
                            confirm_overwrite=True,
                        ),
                        "client_settings": _client_settings_payload(),
                        "table_settings": _table_settings_payload(),
                        "column_settings": {"column_name": "col_a"},
                    },
                )
                assert r_col.status_code == 403
            mock_cls.assert_not_called()

        monkeypatch.setenv("METADATA_WIZARD_ALLOW_REPLACE", "true")
        mock_client = _mock_wizard_client()
        mock_client.accept_table_draft_description.side_effect = ValueError(
            "confirm_overwrite=True required"
        )
        with patch("main.Client", return_value=mock_client):
            r_fail = test_client.post(
                "/accept_table_draft_description",
                headers=headers,
                json={
                    "client_options_settings": _client_options_payload(
                        description_handling="replace",
                        confirm_overwrite=False,
                    ),
                    "client_settings": _client_settings_payload(),
                    "table_settings": _table_settings_payload(),
                },
            )
        assert r_fail.status_code == 400
        mock_client.accept_table_draft_description.assert_called_once_with(
            "allowed-proj.my_ds.my_tbl"
        )
        mock_client._dataplex_ops.update_table_draft_description.assert_not_called()

    def test_llm_project_id_pinned_and_identifier_regex_enforced(self, monkeypatch, test_client):
        monkeypatch.setenv("METADATA_WIZARD_LLM_PROJECT_ID", "pinned-billing-proj")
        mock_client = _mock_wizard_client()
        with patch("main.Client", return_value=mock_client) as mock_cls:
            resp = test_client.post(
                "/generate_table_description",
                headers={"X-API-Key": "test-secret-token-123"},
                json={
                    "client_options_settings": _client_options_payload(),
                    "client_settings": _client_settings_payload("allowed-proj"),
                    "table_settings": _table_settings_payload("allowed-proj"),
                },
            )
            assert resp.status_code == 200
            assert mock_cls.call_args.kwargs["llm_project_id"] == "pinned-billing-proj"

        # SQL injection / invalid chars in table_id -> 422
        resp_bad = test_client.post(
            "/generate_table_description",
            headers={"X-API-Key": "test-secret-token-123"},
            json={
                "client_options_settings": _client_options_payload(),
                "client_settings": _client_settings_payload("allowed-proj"),
                "table_settings": _table_settings_payload(
                    "allowed-proj", "my_ds", "tbl`; DROP TABLE x; --"
                ),
            },
        )
        assert resp_bad.status_code == 422

    def test_review_comment_accept_and_mark_happy_paths(self, test_client):
        headers = {"X-API-Key": "test-secret-token-123"}
        mock_client = _mock_wizard_client()
        with patch("main.Client", return_value=mock_client):
            r_cmt = test_client.post(
                "/metadata/review/allowed-proj.my_ds.my_tbl%23table/comment",
                headers=headers,
                json={
                    "client_settings": _client_settings_payload(),
                    "comment": "<script>alert(1)</script>Please clarify grain",
                },
            )
            assert r_cmt.status_code == 200
            assert r_cmt.json()["comment"]["text"] == "Please clarify grain"
            mock_client.add_comment_to_table_draft_description.assert_called_once_with(
                "allowed-proj.my_ds.my_tbl", "Please clarify grain"
            )

            r_add_cmt = test_client.post(
                "/metadata/review/add_comment",
                headers=headers,
                json={
                    "client_settings": _client_settings_payload(),
                    "table_settings": _table_settings_payload(),
                    "comment": "<b>Keep</b> concise",
                },
            )
            assert r_add_cmt.status_code == 200
            assert r_add_cmt.json()["comment"] == "Keep concise"

            r_acc_tbl = test_client.post(
                "/accept_table_draft_description",
                headers=headers,
                json={
                    "client_options_settings": _client_options_payload(),
                    "client_settings": _client_settings_payload(),
                    "table_settings": _table_settings_payload(),
                },
            )
            assert r_acc_tbl.status_code == 200

            r_acc_col = test_client.post(
                "/accept_column_draft_description",
                headers=headers,
                json={
                    "client_options_settings": _client_options_payload(),
                    "client_settings": _client_settings_payload(),
                    "table_settings": _table_settings_payload(),
                    "column_settings": {"column_name": "col_a"},
                },
            )
            assert r_acc_col.status_code == 200

            r_mark = test_client.post(
                "/mark_for_regeneration",
                headers=headers,
                json={
                    "client_settings": _client_settings_payload(),
                    "request": {"table_fqn": "allowed-proj.my_ds.my_tbl", "column_name": "col_a"},
                },
            )
            assert r_mark.status_code == 200


class TestAPIToRealClientIntegration:
    def _build_mocked_real_client(self, client_cls_kwargs):
        from google.cloud import bigquery
        from dataplexutils.metadata.client import Client as RealClient

        mock_bq = MagicMock()
        mock_table = MagicMock()
        mock_table.description = "Human curated description"
        mock_table.schema = [
            bigquery.SchemaField("col_a", "STRING", mode="NULLABLE", description="Curated col"),
        ]
        mock_bq.get_table.return_value = mock_table
        mock_dataset = MagicMock()
        mock_dataset.location = "US"
        mock_bq.get_dataset.return_value = mock_dataset

        mock_catalog = MagicMock()
        mock_scan = MagicMock()
        mock_lineage = MagicMock()

        with (
            patch("dataplexutils.metadata.client.bigquery.Client", return_value=mock_bq),
            patch(
                "dataplexutils.metadata.client.dataplex_v1.DataScanServiceClient",
                return_value=mock_scan,
            ),
            patch(
                "dataplexutils.metadata.client.datacatalog_lineage_v1.LineageClient",
                return_value=mock_lineage,
            ),
            patch(
                "dataplexutils.metadata.client.dataplex_v1.CatalogServiceClient",
                return_value=mock_catalog,
            ),
        ):
            real_client = RealClient(**client_cls_kwargs)
        return real_client, mock_bq, mock_catalog

    def test_ssrf_blocked_end_to_end_through_api_to_real_client(self, test_client):
        headers = {"X-API-Key": "test-secret-token-123"}

        def factory(**kwargs):
            c, _, _ = self._build_mocked_real_client(kwargs)
            return c

        with (
            patch("main.Client", side_effect=factory),
            patch("dataplexutils.metadata.utils.Part.from_uri") as mock_part,
            patch("dataplexutils.metadata.table_operations.storage.Client") as mock_storage,
        ):
            # use_ext_documents=False with documentation_uri -> 400, no Part.from_uri, no URI echo
            resp = test_client.post(
                "/generate_columns_descriptions",
                headers=headers,
                json={
                    "client_options_settings": _client_options_payload(use_ext_documents=False),
                    "client_settings": _client_settings_payload(),
                    "table_settings": _table_settings_payload(
                        documentation_uri="gs://secret-bucket/secrets.pdf"
                    ),
                },
            )
            assert resp.status_code == 400
            assert "secret-bucket" not in resp.text
            mock_part.assert_not_called()

            # CSV SSRF to disallowed bucket -> 400, no storage.Client call
            resp_csv = test_client.post(
                "/generate_dataset_tables_descriptions",
                headers=headers,
                json={
                    "client_options_settings": _client_options_payload(),
                    "client_settings": _client_settings_payload(),
                    "dataset_settings": _dataset_settings_payload(
                        strategy="DOCUMENTED",
                        documentation_csv_uri="gs://evil-bucket/creds.csv",
                    ),
                },
            )
            assert resp_csv.status_code == 400
            assert "evil-bucket" not in resp_csv.text
            mock_storage.assert_not_called()

    def test_dry_run_and_overwrite_guard_and_regenerate_selected_end_to_end(
        self, monkeypatch, test_client
    ):
        headers = {"X-API-Key": "test-secret-token-123"}
        captured_bqs = []
        captured_catalogs = []

        def factory(**kwargs):
            c, bq, cat = self._build_mocked_real_client(kwargs)
            captured_bqs.append(bq)
            captured_catalogs.append(cat)
            return c

        mock_model = MagicMock()
        mock_model.generate_content.return_value = MagicMock(text="Generated summary")

        with (
            patch("main.Client", side_effect=factory),
            patch("dataplexutils.metadata.utils.vertexai.init"),
            patch("dataplexutils.metadata.utils.GenerativeModel", return_value=mock_model),
        ):
            # 1) dry_run=True performs zero mutations
            r_dry = test_client.post(
                "/generate_table_description",
                headers=headers,
                json={
                    "client_options_settings": _client_options_payload(
                        stage_for_review=False, dry_run=True
                    ),
                    "client_settings": _client_settings_payload(),
                    "table_settings": _table_settings_payload(),
                },
            )
            assert r_dry.status_code == 200
            assert r_dry.json()["dry_run"] is True
            assert r_dry.json()["preview"]["previous_description"] == "Human curated description"
            captured_bqs[-1].update_table.assert_not_called()
            captured_catalogs[-1].update_entry.assert_not_called()

            # 2) Direct replace without confirm_overwrite=True fails even if server flags allow it
            monkeypatch.setenv("METADATA_WIZARD_ALLOW_DIRECT_BQ_WRITE", "true")
            monkeypatch.setenv("METADATA_WIZARD_ALLOW_REPLACE", "true")
            r_no_confirm = test_client.post(
                "/generate_table_description",
                headers=headers,
                json={
                    "client_options_settings": _client_options_payload(
                        stage_for_review=False,
                        description_handling="replace",
                        confirm_overwrite=False,
                    ),
                    "client_settings": _client_settings_payload(),
                    "table_settings": _table_settings_payload(),
                },
            )
            assert r_no_confirm.status_code == 400
            captured_bqs[-1].update_table.assert_not_called()

            # 3) regenerate_selected avoids double-wrap and returns regenerated item
            entry_wrapper = MagicMock()
            entry_wrapper.dataplex_entry.fully_qualified_name = "bigquery:allowed-proj.my_ds.my_tbl"

            def fake_factory(**kwargs):
                c, bq, cat = self._build_mocked_real_client(kwargs)
                cat.search_entries.return_value = [entry_wrapper]
                captured_bqs.append(bq)
                captured_catalogs.append(cat)
                return c

            with patch("main.Client", side_effect=fake_factory):
                r_regen = test_client.post(
                    "/regenerate_selected",
                    headers=headers,
                    json={
                        "client_options_settings": _client_options_payload(
                            stage_for_review=True, dry_run=True
                        ),
                        "client_settings": _client_settings_payload(),
                        "dataset_settings": _dataset_settings_payload(),
                        "regeneration_request": {"objects": ["my_tbl"]},
                    },
                )
            assert r_regen.status_code == 200
            assert r_regen.json()["dry_run"] is True
            assert len(r_regen.json()["preview"]["tables"]) == 1


class TestCLISecurityAndFlags:
    def test_str_to_bool_and_defaults(self, monkeypatch):
        assert cli.str_to_bool("FALSE") is False
        assert cli.str_to_bool("false") is False
        assert cli.str_to_bool("0") is False
        assert cli.str_to_bool("TRUE") is True
        assert cli.str_to_bool("1") is True
        with pytest.raises(argparse.ArgumentTypeError):
            cli.str_to_bool("notabool")

        monkeypatch.setattr(
            sys,
            "argv",
            [
                "metadata_wizard",
                "--service",
                "api.example.com",
                "--scope",
                "table",
                "--dataplex_project_id",
                "allowed-proj",
                "--llm_location",
                "us-central1",
                "--dataplex_location",
                "us-central1",
                "--table_project_id",
                "allowed-proj",
                "--table_dataset_id",
                "ds",
                "--table_id",
                "tbl",
            ],
        )
        args = cli._get_input_arguments()
        assert args.stage_for_review is True
        assert args.dry_run is False
        assert args.confirm_overwrite is False

        monkeypatch.setattr(
            sys,
            "argv",
            [
                "metadata_wizard",
                "--service",
                "api.example.com",
                "--scope",
                "table",
                "--dataplex_project_id",
                "allowed-proj",
                "--llm_location",
                "us-central1",
                "--dataplex_location",
                "us-central1",
                "--table_project_id",
                "allowed-proj",
                "--table_dataset_id",
                "ds",
                "--table_id",
                "tbl",
                "--stage_for_review",
                "FALSE",
                "--dry_run",
                "TRUE",
            ],
        )
        args_override = cli._get_input_arguments()
        assert args_override.stage_for_review is False
        assert args_override.dry_run is True

    def test_cli_sends_x_api_key_and_optional_iam_oidc(self, monkeypatch):
        monkeypatch.setenv("METADATA_WIZARD_API_TOKEN", "cli-secret-token")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"message": "ok"}

        with (
            patch("metadata_wizard_cli.cli.requests.post", return_value=mock_resp) as mock_post,
            patch(
                "metadata_wizard_cli.cli._get_iam_id_token", return_value="iam-oidc-jwt"
            ) as mock_iam,
        ):
            cli._call_api(
                service="wizard.run.app",
                scope="table",
                use_lineage_tables=False,
                use_lineage_processes=False,
                use_profile=False,
                use_data_quality=False,
                use_ext_documents=False,
                persist_to_dataplex_catalog=True,
                stage_for_review=True,
                dataplex_project_id="allowed-proj",
                llm_location="us-central1",
                dataplex_location="us-central1",
                documentation_uri="",
                table_project_id="allowed-proj",
                table_dataset_id="ds",
                table_id="tbl",
                debug=False,
                documentation_csv_uri="",
                strategy="NAIVE",
                top_values_in_description=True,
                dry_run=True,
                confirm_overwrite=False,
                use_iam_auth=True,
            )

        mock_iam.assert_called_once_with("https://wizard.run.app")
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["headers"]["X-API-Key"] == "cli-secret-token"
        assert call_kwargs["headers"]["Authorization"] == "Bearer iam-oidc-jwt"
        assert call_kwargs["json"]["client_options_settings"]["stage_for_review"] is True
        assert call_kwargs["json"]["client_options_settings"]["dry_run"] is True
        assert call_kwargs["timeout"] == 120
