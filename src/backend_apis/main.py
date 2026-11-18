"""
Copyright 2024 Google LLC

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import datetime
import logging
import os
import pkgutil
import re
import secrets
import urllib.parse
import uuid

from fastapi import Body, Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from google.api_core.exceptions import NotFound
from google.auth.transport import requests as google_auth_requests
from google.cloud import dataplex_v1
from google.oauth2 import id_token as google_id_token
from pydantic import BaseModel, ValidationError, field_validator
import toml

from dataplexutils.metadata.client import Client
from dataplexutils.metadata.client_options import ClientOptions
from dataplexutils.metadata.utils import (
    get_max_tables_per_request,
    sanitize_llm_output,
    validate_column_name,
    validate_dataset_id,
    validate_location,
    validate_project_id,
    validate_table_id,
)
from dataplexutils.metadata.version import __version__

# Load constants
constants = toml.loads(pkgutil.get_data("dataplexutils.metadata", "constants.toml").decode())

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_TABLE_FQN_RE = re.compile(
    r"\A([A-Za-z0-9][A-Za-z0-9_-]{0,62})\.([A-Za-z0-9_]{1,1024})\.([A-Za-z0-9_-]{1,1024})\Z"
)
_SEARCH_TERM_RE = re.compile(r"\A[A-Za-z0-9_.-]{1,128}\Z")
_VALID_DESCRIPTION_HANDLINGS = {"append", "prepend", "replace"}
_MAX_DESCRIPTION_LENGTH = constants["DATA"]["MAX_TABLE_DESC_LENGTH"]
_MAX_COMMENT_LENGTH = 2048


def _is_env_true(env_name: str, default: str = "false") -> bool:
    return os.environ.get(env_name, default).strip().lower() in {"true", "1", "yes"}


def _get_cors_origins() -> list[str]:
    """Build an explicit CORS origin allowlist from METADATA_WIZARD_CORS_ORIGINS."""
    raw = os.environ.get("METADATA_WIZARD_CORS_ORIGINS", "").strip()
    if not raw:
        return ["http://localhost:3000"]

    origins: list[str] = []
    for entry in raw.split(","):
        origin = entry.strip().rstrip("/")
        if not origin:
            continue
        if origin == "*":
            logger.warning("Ignoring wildcard '*' in METADATA_WIZARD_CORS_ORIGINS")
            continue
        parsed = urllib.parse.urlparse(origin)
        if parsed.scheme in {"http", "https"} and parsed.netloc and not parsed.path:
            origins.append(origin)
        else:
            logger.warning("Ignoring invalid CORS origin entry")

    return origins or ["http://localhost:3000"]


def get_allowed_projects() -> set[str] | None:
    """Return allowed GCP project IDs, or None only if explicitly opted in."""
    if _is_env_true("METADATA_WIZARD_ALLOW_ANY_PROJECT", "false"):
        return None

    for env_name in (
        "METADATA_WIZARD_ALLOWED_PROJECTS",
        "METADATA_WIZARD_ALLOWED_PROJECT",
        "GOOGLE_CLOUD_PROJECT",
        "GCP_PROJECT",
    ):
        raw = os.environ.get(env_name, "").strip()
        if raw:
            projects = {p.strip() for p in raw.split(",") if p.strip()}
            if projects:
                return projects
    return set()


def enforce_allowed_projects(*project_ids: str | None) -> None:
    """Enforce that all supplied GCP project IDs are explicitly allowlisted."""
    allowed = get_allowed_projects()
    if allowed is None:
        return
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Project allowlist is not configured; access denied",
        )
    for pid in project_ids:
        if pid and pid not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Target project is not permitted by this deployment",
            )


def _constant_time_equals(a: str, b: str) -> bool:
    """Compare strings in constant time using UTF-8 bytes to avoid ASCII-only TypeError."""
    return secrets.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def verify_auth(request: Request) -> str:
    """Fail-closed authentication supporting X-API-Key, Bearer token, and Google OIDC."""
    expected_token = os.environ.get("METADATA_WIZARD_API_TOKEN", "").strip()
    oidc_audience = os.environ.get("METADATA_WIZARD_OIDC_AUDIENCE", "").strip()
    allowed_emails = {
        email.strip().lower()
        for email in os.environ.get("METADATA_WIZARD_OIDC_ALLOWED_EMAILS", "").split(",")
        if email.strip()
    }

    if not expected_token and not oidc_audience:
        logger.error(
            "Authentication is not configured (set METADATA_WIZARD_API_TOKEN or METADATA_WIZARD_OIDC_AUDIENCE)"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server authentication is not configured",
        )

    api_key = request.headers.get("X-API-Key", "").strip()
    if api_key:
        if expected_token and _constant_time_equals(api_key, expected_token):
            return "api_key"
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    auth_header = request.headers.get("Authorization", "")
    scheme, _, bearer_token = auth_header.partition(" ")
    bearer_token = bearer_token.strip() if scheme.lower() == "bearer" else ""

    if bearer_token:
        if expected_token and _constant_time_equals(bearer_token, expected_token):
            return "bearer_token"

        if oidc_audience:
            try:
                claims = google_id_token.verify_oauth2_token(
                    bearer_token,
                    google_auth_requests.Request(),
                    audience=oidc_audience,
                )
                issuer = claims.get("iss")
                if issuer not in {"accounts.google.com", "https://accounts.google.com"}:
                    raise ValueError("Invalid OIDC token issuer")
                if allowed_emails:
                    email = str(claims.get("email", "")).lower()
                    if claims.get("email_verified") is not True or email not in allowed_emails:
                        raise ValueError("OIDC principal is not authorized")
                return str(claims.get("email") or claims.get("sub") or "oidc_user")
            except Exception:
                logger.warning("OIDC bearer token verification failed")

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _validate_table_fqn(table_fqn: str) -> tuple[str, str, str]:
    if not isinstance(table_fqn, str):
        raise ValueError("Invalid table FQN.")
    match = _TABLE_FQN_RE.match(table_fqn.strip())
    if not match:
        raise ValueError("Invalid table FQN.")
    return match.group(1), match.group(2), match.group(3)


def _parse_review_item_id(item_id: str) -> tuple[str, str, str | None]:
    if not isinstance(item_id, str) or not item_id.strip():
        raise ValueError("Invalid review item ID format.")
    cleaned = item_id.strip()

    item_type = None
    table_fqn = None
    column_name = None

    if "#" in cleaned:
        parts = cleaned.split("#")
        if len(parts) == 2 and parts[1] == "table":
            item_type = "table"
            table_fqn = parts[0]
        elif len(parts) == 3 and parts[1] == "column":
            item_type = "column"
            table_fqn = parts[0]
            column_name = parts[2]
        elif len(parts) == 2 and parts[1].startswith("column."):
            item_type = "column"
            table_fqn = parts[0]
            column_name = parts[1][len("column."):]
    elif ":" in cleaned:
        parts = cleaned.split(":")
        if len(parts) == 2 and parts[0] == "table":
            item_type = "table"
            table_fqn = parts[1]
        elif len(parts) == 3 and parts[0] == "column":
            item_type = "column"
            table_fqn = parts[1]
            column_name = parts[2]

    if item_type not in {"table", "column"} or not table_fqn:
        raise ValueError("Invalid review item ID format.")

    project_id, dataset_id, table_id = _validate_table_fqn(table_fqn)
    safe_fqn = f"{project_id}.{dataset_id}.{table_id}"
    if item_type == "column":
        validate_column_name(column_name or "")
    return item_type, safe_fqn, column_name


def _safe_value_error_message(exc: ValueError) -> str:
    msg = str(exc).strip()
    if (
        not msg
        or len(msg) > 220
        or any(token in msg for token in ("gs://", "http://", "https://", "\n", "\r", "Traceback"))
    ):
        return "Invalid request parameters"
    return msg


def _handle_api_exception(exc: Exception, context: str) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, PermissionError):
        logger.warning("%s: permission denied", context)
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied",
        )
    if isinstance(exc, ValueError):
        logger.warning("%s: validation error: %s", context, exc)
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_safe_value_error_message(exc),
        )
    if isinstance(exc, NotFound):
        logger.warning("%s: resource not found", context)
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Requested resource was not found",
        )
    if isinstance(exc, ValidationError):
        logger.warning("%s: payload validation error", context)
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid request parameters",
        )
    ref = str(uuid.uuid4())
    logger.exception("%s [ref=%s]", context, ref)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"Internal server error (ref: {ref})",
    )


class ClientOptionsSettings(BaseModel):
    use_lineage_tables: bool = False
    use_lineage_processes: bool = False
    use_profile: bool = False
    use_data_quality: bool = False
    use_ext_documents: bool = False
    persist_to_dataplex_catalog: bool = True
    stage_for_review: bool = True
    top_values_in_description: bool = True
    description_handling: str = "append"
    description_prefix: str = constants["OUTPUT_CLAUSES"]["AI_WARNING"]
    dry_run: bool = False
    confirm_overwrite: bool = False

    @field_validator("description_handling")
    @classmethod
    def _validate_description_handling(cls, v: str) -> str:
        if not isinstance(v, str) or v.strip().lower() not in _VALID_DESCRIPTION_HANDLINGS:
            raise ValueError("description_handling must be one of: append, prepend, replace")
        return v.strip().lower()


class ClientSettings(BaseModel):
    project_id: str
    llm_location: str
    dataplex_location: str

    @field_validator("project_id")
    @classmethod
    def _check_project_id(cls, v: str) -> str:
        return validate_project_id(v)

    @field_validator("llm_location", "dataplex_location")
    @classmethod
    def _check_location(cls, v: str) -> str:
        return validate_location(v)


class TableSettings(BaseModel):
    project_id: str
    dataset_id: str | None = None
    table_id: str | None = None
    documentation_uri: str | None = None

    @field_validator("project_id")
    @classmethod
    def _check_project_id(cls, v: str) -> str:
        return validate_project_id(v)

    @field_validator("dataset_id")
    @classmethod
    def _check_dataset_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            return None
        return validate_dataset_id(cleaned)

    @field_validator("table_id")
    @classmethod
    def _check_table_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            return None
        return validate_table_id(cleaned)

    @field_validator("documentation_uri")
    @classmethod
    def _normalize_doc_uri(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        return cleaned or None


class DatasetSettings(BaseModel):
    project_id: str
    dataset_id: str | None = None
    documentation_csv_uri: str | None = None
    strategy: str = "NAIVE"

    @field_validator("project_id")
    @classmethod
    def _check_project_id(cls, v: str) -> str:
        return validate_project_id(v)

    @field_validator("dataset_id")
    @classmethod
    def _check_dataset_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            return None
        return validate_dataset_id(cleaned)

    @field_validator("documentation_csv_uri")
    @classmethod
    def _normalize_csv_uri(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        return cleaned or None


class ColumnSettings(BaseModel):
    column_name: str

    @field_validator("column_name")
    @classmethod
    def _check_column_name(cls, v: str) -> str:
        return validate_column_name(v)


class RegenerationCounts(BaseModel):
    tables: int
    columns: int


class RegenerationRequest(BaseModel):
    objects: list[str] = []

    @field_validator("objects")
    @classmethod
    def _check_objects(cls, values: list[str]) -> list[str]:
        cleaned_list: list[str] = []
        for item in values:
            if not isinstance(item, str):
                raise ValueError("Invalid regeneration filter.")
            cleaned = item.strip()
            if not cleaned:
                continue
            if cleaned.upper() in {"AND", "OR", "NOT"} or not _SEARCH_TERM_RE.match(cleaned):
                raise ValueError("Invalid regeneration filter.")
            cleaned_list.append(cleaned)
        return cleaned_list


class MarkForRegenerationRequest(BaseModel):
    table_fqn: str
    column_name: str | None = None

    @field_validator("table_fqn")
    @classmethod
    def _check_table_fqn(cls, v: str) -> str:
        project_id, dataset_id, table_id = _validate_table_fqn(v)
        return f"{project_id}.{dataset_id}.{table_id}"

    @field_validator("column_name")
    @classmethod
    def _check_column_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            return None
        return validate_column_name(cleaned)


class UpdateDraftDescriptionRequest(BaseModel):
    client_settings: ClientSettings
    table_settings: TableSettings
    description: str
    is_html: bool = False

    @field_validator("description")
    @classmethod
    def _check_description(cls, v: str) -> str:
        if not isinstance(v, str):
            raise ValueError("Description must be a string.")
        if len(v) > _MAX_DESCRIPTION_LENGTH:
            raise ValueError("Description exceeds maximum allowed length.")
        return v


class AddCommentRequest(BaseModel):
    client_settings: ClientSettings
    table_settings: TableSettings
    comment: str
    column_name: str | None = None

    @field_validator("comment")
    @classmethod
    def _check_comment(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Comment must be a non-empty string.")
        if len(v) > _MAX_COMMENT_LENGTH:
            raise ValueError("Comment exceeds maximum allowed length.")
        cleaned = sanitize_llm_output(v.strip(), max_chars=_MAX_COMMENT_LENGTH)
        if not cleaned:
            raise ValueError("Comment must be a non-empty string.")
        return cleaned

    @field_validator("column_name")
    @classmethod
    def _check_column_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            return None
        return validate_column_name(cleaned)


class AddNegativeExampleRequest(BaseModel):
    client_settings: ClientSettings
    table_settings: TableSettings
    example: str

    @field_validator("example")
    @classmethod
    def _check_example(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Negative example must be a non-empty string.")
        if len(v) > _MAX_COMMENT_LENGTH:
            raise ValueError("Negative example exceeds maximum allowed length.")
        cleaned = sanitize_llm_output(v.strip(), max_chars=_MAX_COMMENT_LENGTH)
        if not cleaned:
            raise ValueError("Negative example must be a non-empty string.")
        return cleaned


def _enforce_replace_policy(client_options_settings: ClientOptionsSettings) -> None:
    allow_replace = _is_env_true("METADATA_WIZARD_ALLOW_REPLACE", "false")
    if (
        not allow_replace
        and client_options_settings.description_handling.strip().lower() == "replace"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Destructive 'replace' description handling is disabled by server policy",
        )


def _enforce_generation_policy(client_options_settings: ClientOptionsSettings) -> None:
    allow_direct = _is_env_true("METADATA_WIZARD_ALLOW_DIRECT_BQ_WRITE", "false")

    if (
        not allow_direct
        and not client_options_settings.dry_run
        and not client_options_settings.stage_for_review
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Direct BigQuery writes are disabled; enable stage_for_review or dry_run",
        )
    _enforce_replace_policy(client_options_settings)


def _build_client_options(settings: ClientOptionsSettings) -> ClientOptions:
    return ClientOptions(
        use_lineage_tables=settings.use_lineage_tables,
        use_lineage_processes=settings.use_lineage_processes,
        use_profile=settings.use_profile,
        use_data_quality=settings.use_data_quality,
        use_ext_documents=settings.use_ext_documents,
        persist_to_dataplex_catalog=settings.persist_to_dataplex_catalog,
        stage_for_review=settings.stage_for_review,
        top_values_in_description=settings.top_values_in_description,
        description_handling=settings.description_handling,
        description_prefix=settings.description_prefix,
        dry_run=settings.dry_run,
        confirm_overwrite=settings.confirm_overwrite,
    )


def _build_client(
    client_settings: ClientSettings, client_options: ClientOptions | None = None
) -> Client:
    llm_project_id = os.environ.get("METADATA_WIZARD_LLM_PROJECT_ID", "").strip() or None
    if llm_project_id:
        validate_project_id(llm_project_id)
    return Client(
        project_id=client_settings.project_id,
        llm_location=client_settings.llm_location,
        dataplex_location=client_settings.dataplex_location,
        client_options=client_options,
        llm_project_id=llm_project_id,
    )


def _require_table_fqn(table_settings: TableSettings) -> str:
    if not table_settings.dataset_id or not table_settings.table_id:
        raise ValueError("dataset_id and table_id are required.")
    return f"{table_settings.project_id}.{table_settings.dataset_id}.{table_settings.table_id}"


def _require_dataset_fqn(dataset_settings: DatasetSettings) -> str:
    if not dataset_settings.dataset_id:
        raise ValueError("dataset_id is required.")
    return f"{dataset_settings.project_id}.{dataset_settings.dataset_id}"


app = FastAPI(
    dependencies=[Depends(verify_auth)],
    openapi_url=None,
    docs_url=None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    logger.warning("HTTP %s on %s: %s", exc.status_code, request.url.path, exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=getattr(exc, "headers", None),
    )


@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info("Request: %s %s", request.method, request.url.path)
    return await call_next(request)


@app.get("/version")
def read_version():
    return {"version": __version__}


@app.post("/generate_table_description")
def generate_table_description(
    client_options_settings: ClientOptionsSettings = Body(),
    client_settings: ClientSettings = Body(),
    table_settings: TableSettings = Body(),
    dataset_settings: DatasetSettings | None = Body(default=None),
):
    """Generates a table description using the provided settings."""
    try:
        enforce_allowed_projects(
            client_settings.project_id,
            table_settings.project_id,
            dataset_settings.project_id if dataset_settings else None,
        )
        _enforce_generation_policy(client_options_settings)
        table_fqn = _require_table_fqn(table_settings)

        client_options = _build_client_options(client_options_settings)
        client = _build_client(client_settings, client_options)

        logger.info("Generating table description for %s", table_fqn)
        result = client.generate_table_description(table_fqn, table_settings.documentation_uri)
        if client_options_settings.dry_run:
            return {
                "message": "Dry-run preview generated successfully",
                "dry_run": True,
                "preview": result,
            }
        return {"message": "Table description generated successfully"}
    except Exception as e:
        raise _handle_api_exception(e, "generate_table_description")


@app.post("/generate_columns_descriptions")
def generate_columns_descriptions(
    client_options_settings: ClientOptionsSettings = Body(),
    client_settings: ClientSettings = Body(),
    table_settings: TableSettings = Body(),
    dataset_settings: DatasetSettings | None = Body(default=None),
):
    """Generates column descriptions for a table using the provided settings."""
    try:
        enforce_allowed_projects(
            client_settings.project_id,
            table_settings.project_id,
            dataset_settings.project_id if dataset_settings else None,
        )
        _enforce_generation_policy(client_options_settings)
        table_fqn = _require_table_fqn(table_settings)

        client_options = _build_client_options(client_options_settings)
        client = _build_client(client_settings, client_options)

        logger.info("Generating column descriptions for %s", table_fqn)
        result = client.generate_columns_descriptions(table_fqn, table_settings.documentation_uri)
        if client_options_settings.dry_run:
            return {
                "message": "Dry-run column preview generated successfully",
                "dry_run": True,
                "preview": result,
            }
        return {"message": "Column descriptions generated successfully"}
    except Exception as e:
        raise _handle_api_exception(e, "generate_columns_descriptions")


@app.post("/generate_dataset_tables_descriptions")
def generate_dataset_tables_descriptions(
    client_options_settings: ClientOptionsSettings = Body(),
    client_settings: ClientSettings = Body(),
    table_settings: TableSettings | None = Body(default=None),
    dataset_settings: DatasetSettings = Body(),
):
    """Generates table descriptions for a dataset using the provided settings."""
    try:
        enforce_allowed_projects(
            client_settings.project_id,
            dataset_settings.project_id,
            table_settings.project_id if table_settings else None,
        )
        _enforce_generation_policy(client_options_settings)
        dataset_fqn = _require_dataset_fqn(dataset_settings)

        client_options = _build_client_options(client_options_settings)
        client = _build_client(client_settings, client_options)

        logger.info("Generating dataset table descriptions for %s", dataset_fqn)
        result = client.generate_dataset_tables_descriptions(
            dataset_fqn, dataset_settings.strategy, dataset_settings.documentation_csv_uri
        )
        if client_options_settings.dry_run:
            return {
                "message": "Dry-run dataset table preview generated successfully",
                "dry_run": True,
                "preview": result,
            }
        return {"message": "Dataset table descriptions generated successfully"}
    except Exception as e:
        raise _handle_api_exception(e, "generate_dataset_tables_descriptions")


@app.post("/generate_dataset_tables_columns_descriptions")
def generate_dataset_tables_columns_descriptions(
    client_options_settings: ClientOptionsSettings = Body(),
    client_settings: ClientSettings = Body(),
    table_settings: TableSettings | None = Body(default=None),
    dataset_settings: DatasetSettings = Body(),
):
    """Generates table and column descriptions for a dataset."""
    try:
        enforce_allowed_projects(
            client_settings.project_id,
            dataset_settings.project_id,
            table_settings.project_id if table_settings else None,
        )
        _enforce_generation_policy(client_options_settings)
        dataset_fqn = _require_dataset_fqn(dataset_settings)

        client_options = _build_client_options(client_options_settings)
        client = _build_client(client_settings, client_options)

        logger.info("Generating dataset table and column descriptions for %s", dataset_fqn)
        result = client.generate_dataset_tables_columns_descriptions(
            dataset_fqn, dataset_settings.strategy, dataset_settings.documentation_csv_uri
        )
        if client_options_settings.dry_run:
            return {
                "message": "Dry-run dataset tables and columns preview generated successfully",
                "dry_run": True,
                "preview": result,
            }
        return {"message": "Dataset table columns descriptions generated successfully"}
    except Exception as e:
        raise _handle_api_exception(e, "generate_dataset_tables_columns_descriptions")


@app.post("/accept_table_draft_description")
def accept_table_draft_description(
    principal: str = Depends(verify_auth),
    client_options_settings: ClientOptionsSettings = Body(),
    client_settings: ClientSettings = Body(),
    table_settings: TableSettings = Body(),
    dataset_settings: DatasetSettings | None = Body(default=None),
):
    """Accepts the draft description for a table, promoting it to the actual description."""
    try:
        enforce_allowed_projects(
            client_settings.project_id,
            table_settings.project_id,
            dataset_settings.project_id if dataset_settings else None,
        )
        _enforce_replace_policy(client_options_settings)
        table_fqn = _require_table_fqn(table_settings)

        client_options = _build_client_options(client_options_settings)
        client = _build_client(client_settings, client_options)

        logger.info("Accepting draft description for table %s", table_fqn)
        existing_comments = client.get_comments_to_table_draft_description(table_fqn) or []
        existing_negative_examples = (
            client.get_negative_examples_to_table_draft_description(table_fqn) or []
        )
        details = client.get_review_item_details(table_fqn)
        draft_description = (details or {}).get("draftDescription", "")
        if not draft_description:
            raise ValueError("No draft description found for table.")

        # Promote to BigQuery & Dataplex overview BEFORE marking draft as accepted
        accepted = client.accept_table_draft_description(table_fqn)
        if not accepted:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to accept table draft description",
            )

        now_iso = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        aspect_content = {
            "certified": "true",
            "user-who-certified": principal or "system",
            "contents": draft_description,
            "generation-date": now_iso,
            "to-be-regenerated": "false",
            "human-comments": existing_comments,
            "negative-examples": existing_negative_examples,
            "external-document-uri": table_settings.documentation_uri or "",
            "is-accepted": True,
            "when-accepted": now_iso,
        }

        success = client._dataplex_ops.update_table_draft_description(
            table_fqn=table_fqn,
            description=draft_description,
            metadata=aspect_content,
        )
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update aspect metadata",
            )

        return {"message": "Table draft description accepted successfully"}
    except Exception as e:
        raise _handle_api_exception(e, "accept_table_draft_description")


@app.post("/accept_column_draft_description")
def accept_column_draft_description(
    client_options_settings: ClientOptionsSettings = Body(),
    client_settings: ClientSettings = Body(),
    table_settings: TableSettings = Body(),
    dataset_settings: DatasetSettings | None = Body(default=None),
    column_settings: ColumnSettings = Body(),
):
    """Accepts the draft description for a column, promoting it to the actual column description."""
    try:
        enforce_allowed_projects(
            client_settings.project_id,
            table_settings.project_id,
            dataset_settings.project_id if dataset_settings else None,
        )
        _enforce_replace_policy(client_options_settings)
        table_fqn = _require_table_fqn(table_settings)
        column_name = column_settings.column_name

        client_options = _build_client_options(client_options_settings)
        client = _build_client(client_settings, client_options)

        logger.info("Accepting draft description for column %s.%s", table_fqn, column_name)
        accepted = client.accept_column_draft_description(table_fqn, column_name)
        if not accepted:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to accept column draft description",
            )

        return {"message": f"Column {column_name} draft description accepted successfully"}
    except Exception as e:
        raise _handle_api_exception(e, "accept_column_draft_description")


@app.post("/get_regeneration_counts")
def get_regeneration_counts(
    client_settings: ClientSettings = Body(),
    dataset_settings: DatasetSettings = Body(),
    search_query: str | None = Body(default=None),
):
    try:
        enforce_allowed_projects(client_settings.project_id, dataset_settings.project_id)
        dataset_fqn = _require_dataset_fqn(dataset_settings)
        if search_query is not None:
            cleaned_search = search_query.strip()
            if cleaned_search and (
                cleaned_search.upper() in {"AND", "OR", "NOT"}
                or not _SEARCH_TERM_RE.match(cleaned_search)
            ):
                raise ValueError("Invalid search query filter.")

        client = _build_client(client_settings)
        logger.info("Getting regeneration counts for dataset %s", dataset_fqn)
        tables = client._table_ops._list_tables_in_dataset_for_regeneration(dataset_fqn)
        return RegenerationCounts(tables=len(tables), columns=0)
    except Exception as e:
        raise _handle_api_exception(e, "get_regeneration_counts")


@app.get("/get_regeneration_counts")
def get_regeneration_counts_get(
    project_id: str,
    llm_location: str,
    dataplex_location: str,
    dataset_project_id: str,
    dataset_id: str,
    search_query: str | None = None,
):
    try:
        client_settings = ClientSettings(
            project_id=project_id,
            llm_location=llm_location,
            dataplex_location=dataplex_location,
        )
        dataset_settings = DatasetSettings(
            project_id=dataset_project_id,
            dataset_id=dataset_id,
            documentation_csv_uri=None,
            strategy="NAIVE",
        )
        return get_regeneration_counts(
            client_settings=client_settings,
            dataset_settings=dataset_settings,
            search_query=search_query,
        )
    except Exception as e:
        raise _handle_api_exception(e, "get_regeneration_counts_get")


@app.post("/regenerate_selected")
def regenerate_selected(
    client_options_settings: ClientOptionsSettings = Body(),
    client_settings: ClientSettings = Body(),
    dataset_settings: DatasetSettings = Body(),
    regeneration_request: RegenerationRequest = Body(),
):
    try:
        enforce_allowed_projects(client_settings.project_id, dataset_settings.project_id)
        for obj in regeneration_request.objects:
            if _TABLE_FQN_RE.match(obj):
                enforce_allowed_projects(obj.split(".", 1)[0])
        _enforce_generation_policy(client_options_settings)
        dataset_fqn = _require_dataset_fqn(dataset_settings)

        client_options = _build_client_options(client_options_settings)
        client_options._regenerate = True
        client_options._use_human_comments = True
        client = _build_client(client_settings, client_options)

        search_query = regeneration_request.objects[0] if regeneration_request.objects else None

        effective_query = client._review_ops.build_search_query_for_regeneration(
            dataset_fqn, search_query
        )
        catalog_client = client._cloud_clients[constants["CLIENTS"]["DATAPLEX_CATALOG"]]
        max_tables = get_max_tables_per_request()
        search_request = dataplex_v1.SearchEntriesRequest(
            name=f"projects/{client._project_id}/locations/global",
            query=effective_query,
            page_size=max_tables,
        )
        search_results = catalog_client.search_entries(request=search_request)

        results = []
        previews = []
        for entry_result in search_results:
            entry = getattr(entry_result, "dataplex_entry", None)
            fqn = getattr(entry, "fully_qualified_name", "") if entry else ""
            if not fqn.startswith("bigquery:"):
                continue
            table_fqn = fqn.replace("bigquery:", "", 1)
            tbl_project, tbl_dataset, _ = _validate_table_fqn(table_fqn)
            if f"{tbl_project}.{tbl_dataset}" != dataset_fqn:
                continue
            enforce_allowed_projects(tbl_project)

            if len(results) >= max_tables:
                raise ValueError(
                    f"Dataset operation exceeds maximum tables per request ({max_tables})."
                )

            res = client.generate_table_description(table_fqn)
            results.append(
                {
                    "object": table_fqn,
                    "status": "dry_run" if client_options_settings.dry_run else "regenerated",
                }
            )
            if client_options_settings.dry_run:
                previews.append(res)

        if client_options_settings.dry_run:
            return {
                "message": "Dry-run selective regeneration preview generated successfully",
                "dry_run": True,
                "preview": {"dataset": dataset_fqn, "tables": previews},
            }
        return {"regenerated_objects": results}
    except Exception as e:
        raise _handle_api_exception(e, "regenerate_selected")


@app.post("/regenerate_all")
def regenerate_all(
    client_options_settings: ClientOptionsSettings = Body(),
    client_settings: ClientSettings = Body(),
    dataset_settings: DatasetSettings = Body(),
):
    try:
        enforce_allowed_projects(client_settings.project_id, dataset_settings.project_id)
        _enforce_generation_policy(client_options_settings)
        dataset_fqn = _require_dataset_fqn(dataset_settings)

        client_options = _build_client_options(client_options_settings)
        client_options._regenerate = True
        client = _build_client(client_settings, client_options)

        logger.info("Regenerating all marked items in dataset %s", dataset_fqn)
        result = client.regenerate_dataset_tables_columns_descriptions(
            dataset_fqn=dataset_fqn,
            strategy=dataset_settings.strategy,
            documentation_csv_uri=dataset_settings.documentation_csv_uri,
        )
        client._client_options._regenerate = False

        if client_options_settings.dry_run:
            return {
                "message": "Dry-run regeneration preview generated successfully",
                "dry_run": True,
                "preview": result,
            }
        return {"message": "All marked items (tables and columns) regenerated successfully"}
    except Exception as e:
        raise _handle_api_exception(e, "regenerate_all")


class Comment(BaseModel):
    id: str
    text: str
    type: str
    timestamp: str


class MetadataItem(BaseModel):
    id: str
    type: str
    name: str
    currentDescription: str
    draftDescription: str
    isHtml: bool
    status: str
    lastModified: str
    comments: list[Comment]
    markedForRegeneration: bool = False


@app.post("/metadata/review")
def get_review_items(
    client_settings: ClientSettings = Body(),
    dataset_settings: DatasetSettings = Body(),
):
    try:
        enforce_allowed_projects(client_settings.project_id, dataset_settings.project_id)

        dataset_fqn = None
        if dataset_settings.dataset_id:
            dataset_fqn = f"{dataset_settings.project_id}.{dataset_settings.dataset_id}"

        client = _build_client(client_settings)
        result = client._review_ops.get_review_items_for_dataset(dataset_fqn=dataset_fqn)

        raw_items = []
        if isinstance(result, dict):
            if "data" in result and isinstance(result["data"], dict):
                raw_items = result["data"].get("items", [])
            else:
                raw_items = result.get("items", [])

        if not isinstance(raw_items, list):
            raw_items = []

        allowed_projects = get_allowed_projects()
        filtered_items = []
        for item in raw_items:
            if isinstance(item, dict):
                review_metadata = item.get("metadata", {})
                if isinstance(review_metadata, dict) and review_metadata.get("is-accepted") is True:
                    continue
                if allowed_projects is not None:
                    item_id = str(item.get("id", ""))
                    item_fqn = item_id.split("#", 1)[0] if "#" in item_id else str(item.get("name", ""))
                    item_project = item_fqn.split(".", 1)[0] if "." in item_fqn else ""
                    if not item_project or item_project not in allowed_projects:
                        continue
                filtered_items.append(item)

        if not isinstance(result, dict):
            result = {"items": [], "nextPageToken": None, "totalCount": 0}
        if isinstance(result, dict) and "data" in result and isinstance(result["data"], dict):
            result = result["data"]

        return {
            "items": filtered_items,
            "nextPageToken": result.get("nextPageToken", None),
            "totalCount": len(filtered_items),
        }
    except Exception as e:
        raise _handle_api_exception(e, "get_review_items")


@app.post("/metadata/review/{id}/reject")
def reject_review_item(
    id: str,
    client_settings: ClientSettings = Body(),
):
    try:
        _, table_fqn, _ = _parse_review_item_id(id)
        target_project, _, _ = _validate_table_fqn(table_fqn)
        enforce_allowed_projects(client_settings.project_id, target_project)

        client = _build_client(client_settings)
        result = client.reject_review_item(id)
        if isinstance(result, dict) and result.get("success") is False:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to reject review item",
            )
        return {"status": "rejected", "id": id, **result}
    except Exception as e:
        raise _handle_api_exception(e, "reject_review_item")


@app.post("/metadata/review/{id}/edit")
def edit_review_item(
    id: str,
    client_settings: ClientSettings = Body(),
    description: str = Body(..., embed=True),
):
    try:
        item_type, table_fqn, _ = _parse_review_item_id(id)
        target_project, _, _ = _validate_table_fqn(table_fqn)
        enforce_allowed_projects(client_settings.project_id, target_project)

        if not isinstance(description, str):
            raise ValueError("Description must be a string.")
        max_len = (
            constants["DATA"]["MAX_TABLE_DESC_LENGTH"]
            if item_type == "table"
            else constants["DATA"]["MAX_COLUMN_DESC_LENGTH"]
        )
        if len(description) > max_len:
            raise ValueError("Description exceeds maximum allowed length.")

        client = _build_client(client_settings)
        result = client.edit_review_item(id, description)
        if isinstance(result, dict) and result.get("success") is False:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to edit review item",
            )
        return {"status": "updated", "id": id, **result}
    except Exception as e:
        raise _handle_api_exception(e, "edit_review_item")


@app.post("/metadata/review/{id}/comment")
def add_review_comment(
    id: str,
    client_settings: ClientSettings = Body(),
    comment: str = Body(..., embed=True),
):
    try:
        item_type, table_fqn, column_name = _parse_review_item_id(id)
        target_project, _, _ = _validate_table_fqn(table_fqn)
        enforce_allowed_projects(client_settings.project_id, target_project)

        if not isinstance(comment, str) or not comment.strip():
            raise ValueError("Comment must be a non-empty string.")
        if len(comment) > _MAX_COMMENT_LENGTH:
            raise ValueError("Comment exceeds maximum allowed length.")
        clean_comment = sanitize_llm_output(comment, max_chars=_MAX_COMMENT_LENGTH)
        if not clean_comment:
            raise ValueError("Comment must be a non-empty string.")

        client = _build_client(client_settings)
        if item_type == "column":
            success = client.add_comment_to_column_draft_description(
                table_fqn, column_name, clean_comment
            )
        else:
            success = client.add_comment_to_table_draft_description(table_fqn, clean_comment)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to add comment",
            )

        return {
            "status": "added",
            "id": id,
            "comment": {
                "id": str(uuid.uuid4()),
                "text": clean_comment,
                "type": "human",
                "timestamp": datetime.datetime.now().isoformat(),
            },
        }
    except Exception as e:
        raise _handle_api_exception(e, "add_review_comment")


@app.post("/mark_for_regeneration")
def mark_for_regeneration(
    client_settings: ClientSettings = Body(),
    request: MarkForRegenerationRequest = Body(),
):
    """Mark a table or column for regeneration."""
    try:
        target_project, _, _ = _validate_table_fqn(request.table_fqn)
        enforce_allowed_projects(client_settings.project_id, target_project)

        client = _build_client(client_settings)
        if request.column_name:
            success = client.mark_column_for_regeneration(request.table_fqn, request.column_name)
            if success:
                return {
                    "message": f"Column {request.column_name} in table {request.table_fqn} marked for regeneration"
                }
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to mark column for regeneration",
            )

        success = client.mark_table_for_regeneration(request.table_fqn)
        if success:
            return {"message": f"Table {request.table_fqn} marked for regeneration"}
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to mark table for regeneration",
        )
    except Exception as e:
        raise _handle_api_exception(e, "mark_for_regeneration")


@app.post("/metadata/review/details")
def get_review_item_details(
    client_settings: ClientSettings = Body(),
    table_settings: TableSettings = Body(),
    column_name: str | None = Body(default=None),
):
    """Get detailed information about a review item."""
    table_fqn = None
    try:
        enforce_allowed_projects(client_settings.project_id, table_settings.project_id)
        table_fqn = _require_table_fqn(table_settings)
        if column_name is not None:
            column_name = column_name.strip() or None
            if column_name is not None:
                validate_column_name(column_name)

        client = _build_client(client_settings)
        if column_name:
            details = client.get_review_item_details(table_fqn, column_name)
        else:
            details = client.get_review_item_details(table_fqn)

        if not details:
            raise NotFound(message="Review item not found")

        return details
    except Exception as e:
        raise _handle_api_exception(e, "get_review_item_details")


@app.post("/update_table_draft_description")
def update_table_draft_description(update_request: UpdateDraftDescriptionRequest):
    """Update the draft description for a table."""
    try:
        enforce_allowed_projects(
            update_request.client_settings.project_id,
            update_request.table_settings.project_id,
        )
        table_fqn = _require_table_fqn(update_request.table_settings)

        client = _build_client(update_request.client_settings)
        logger.info(
            "Updating draft description for %s (len=%d, is_html=%s)",
            table_fqn,
            len(update_request.description),
            update_request.is_html,
        )

        success = client._dataplex_ops.update_table_draft_description(
            table_fqn=table_fqn,
            description=update_request.description,
        )
        if success:
            return {
                "status": "success",
                "message": "Draft description updated successfully",
            }
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update draft description",
        )
    except Exception as e:
        raise _handle_api_exception(e, "update_table_draft_description")


@app.post("/metadata/review/add_comment")
def add_comment(request: AddCommentRequest):
    """Add a comment to a table or column's draft description."""
    try:
        enforce_allowed_projects(
            request.client_settings.project_id,
            request.table_settings.project_id,
        )
        table_fqn = _require_table_fqn(request.table_settings)

        client = _build_client(request.client_settings)
        if request.column_name:
            success = client.add_comment_to_column_draft_description(
                table_fqn, request.column_name, request.comment
            )
        else:
            success = client.add_comment_to_table_draft_description(table_fqn, request.comment)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to add comment",
            )

        return {"comment": request.comment}
    except Exception as e:
        raise _handle_api_exception(e, "add_comment")


@app.post("/metadata/review/add_negative_example")
def add_negative_example(request: AddNegativeExampleRequest):
    """Add a negative example to a table's draft description."""
    try:
        enforce_allowed_projects(
            request.client_settings.project_id,
            request.table_settings.project_id,
        )
        table_fqn = _require_table_fqn(request.table_settings)

        client = _build_client(request.client_settings)
        existing_comments = client.get_comments_to_table_draft_description(table_fqn) or []
        existing_negative_examples = list(
            client.get_negative_examples_to_table_draft_description(table_fqn) or []
        )
        existing_negative_examples.append(request.example)

        aspect_content = {
            "negative-examples": existing_negative_examples,
            "human-comments": existing_comments,
        }
        details = client.get_review_item_details(table_fqn)
        draft_description = (details or {}).get("draftDescription", "")

        success = client._dataplex_ops.update_table_draft_description(
            table_fqn=table_fqn,
            description=draft_description,
            metadata=aspect_content,
        )
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to add negative example",
            )

        return {"example": request.example}
    except Exception as e:
        raise _handle_api_exception(e, "add_negative_example")
