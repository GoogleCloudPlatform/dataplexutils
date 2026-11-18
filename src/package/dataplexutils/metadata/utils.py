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
"""Dataplex Utils Metadata Wizard utility functions
   2024 Google
"""
# Standard library imports
import html
import json
import logging
import os
import pkgutil
import re
import time
import urllib.parse

# Cloud imports
import google.api_core.exceptions
import toml
import vertexai
from vertexai.generative_models import GenerationConfig, GenerativeModel, Part
import vertexai.preview.generative_models as generative_models

# Load constants
constants = toml.loads(pkgutil.get_data(__name__, "constants.toml").decode())
# Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(constants["LOGGING"]["WIZARD_LOGGER"])

_PROJECT_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_-]{0,62}\Z")
_LOCATION_RE = re.compile(r"\A[a-z0-9][a-z0-9-]{0,62}\Z")
_DATASET_ID_RE = re.compile(r"\A[A-Za-z0-9_]{1,1024}\Z")
_TABLE_ID_RE = re.compile(r"\A[A-Za-z0-9_-]{1,1024}\Z")
_COLUMN_NAME_RE = re.compile(r"\A[A-Za-z0-9_][A-Za-z0-9_-]{0,299}\Z")
_BQ_JOB_ID_RE = re.compile(r"\A[A-Za-z0-9_-]{1,1024}\Z")
_GCS_BUCKET_RE = re.compile(r"\A[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]\Z")
_TABLE_FQN_RE = re.compile(
    r"\A([A-Za-z0-9][A-Za-z0-9_-]{0,62})\.([A-Za-z0-9_]{1,1024})\.([A-Za-z0-9_-]{1,1024})\Z"
)
_DATASET_FQN_RE = re.compile(
    r"\A([A-Za-z0-9][A-Za-z0-9_-]{0,62})\.([A-Za-z0-9_]{1,1024})\Z"
)

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BIDI_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")
_DANGEROUS_HTML_BLOCKS_RE = re.compile(
    r"(?is)<\s*(script|style|iframe|object|embed|svg|math)\b(?:[^<>\"']|\"[^\"]*\"|'[^']*')*(?:>.*?(?:<\s*/\s*\1\s*>|\Z)|\Z)"
)
_HTML_TAG_RE = re.compile(
    r"<[A-Za-z/!?](?:[^<>\"']|\"[^\"]*\"|'[^']*')*(?:>|\Z)"
)
_DANGEROUS_URI_SCHEME_RE = re.compile(
    r"(?is)(?:(?:javascript|vbscript)\s*:|data\s*:\s*text/html)"
)

_RETRYABLE_EXCEPTIONS = tuple(
    exc
    for exc in (
        getattr(google.api_core.exceptions, "ResourceExhausted", None),
        getattr(google.api_core.exceptions, "ServiceUnavailable", None),
        getattr(google.api_core.exceptions, "DeadlineExceeded", None),
        getattr(google.api_core.exceptions, "TooManyRequests", None),
    )
    if exc is not None
)


def _get_positive_int_env(env_name: str, default: int) -> int:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid integer configuration for {env_name}.") from exc
    if value <= 0:
        raise ValueError(f"Configuration {env_name} must be positive.")
    return value


def get_max_tables_per_request() -> int:
    return _get_positive_int_env("METADATA_WIZARD_MAX_TABLES_PER_REQUEST", 50)


def get_max_columns_per_request() -> int:
    return _get_positive_int_env("METADATA_WIZARD_MAX_COLUMNS_PER_REQUEST", 200)


def get_max_llm_calls_per_request() -> int:
    return _get_positive_int_env("METADATA_WIZARD_MAX_LLM_CALLS_PER_REQUEST", 500)


def get_max_csv_bytes() -> int:
    return _get_positive_int_env("METADATA_WIZARD_MAX_CSV_BYTES", 1048576)


def get_allowed_doc_buckets() -> set[str]:
    raw = os.environ.get("METADATA_WIZARD_ALLOWED_DOC_BUCKETS", "")
    return {bucket.strip() for bucket in raw.split(",") if bucket.strip()}


def validate_project_id(project_id: str) -> str:
    if not isinstance(project_id, str) or not _PROJECT_ID_RE.match(project_id):
        raise ValueError("Invalid GCP project_id.")
    return project_id


def validate_location(location: str) -> str:
    if not isinstance(location, str) or not _LOCATION_RE.match(location):
        raise ValueError("Invalid GCP location.")
    return location


def validate_dataset_id(dataset_id: str) -> str:
    if not isinstance(dataset_id, str) or not _DATASET_ID_RE.match(dataset_id):
        raise ValueError("Invalid BigQuery dataset_id.")
    return dataset_id


def validate_table_id(table_id: str) -> str:
    if not isinstance(table_id, str) or not _TABLE_ID_RE.match(table_id):
        raise ValueError("Invalid BigQuery table_id.")
    return table_id


def validate_column_name(column_name: str) -> str:
    if not isinstance(column_name, str) or not _COLUMN_NAME_RE.match(column_name):
        raise ValueError("Invalid BigQuery column_name.")
    return column_name


def validate_bq_job_id(bq_job_id: str) -> str:
    if not isinstance(bq_job_id, str) or not bq_job_id:
        raise ValueError("Invalid BigQuery job ID.")
    if ":" in bq_job_id:
        proj, rest = bq_job_id.split(":", 1)
        if "." not in rest:
            raise ValueError("Invalid BigQuery job ID.")
        loc, job_part = rest.split(".", 1)
        validate_project_id(proj)
        validate_location(loc)
        if not _BQ_JOB_ID_RE.match(job_part):
            raise ValueError("Invalid BigQuery job ID.")
        return bq_job_id
    if not _BQ_JOB_ID_RE.match(bq_job_id):
        raise ValueError("Invalid BigQuery job ID.")
    return bq_job_id


def validate_gcs_uri(uri: str) -> tuple[str, str]:
    """Validate that a URI is an allowed gs://bucket/object URI."""
    if not isinstance(uri, str) or not uri:
        raise ValueError("Invalid documentation URI.")
    if "\\" in uri or "%" in uri:
        raise ValueError("Invalid documentation URI.")
    if any(ord(ch) < 32 or ord(ch) == 127 or ch.isspace() for ch in uri):
        raise ValueError("Invalid documentation URI.")
    unquoted = urllib.parse.unquote(uri)
    if unquoted != uri:
        raise ValueError("Invalid documentation URI.")
    parsed = urllib.parse.urlparse(uri)
    if (
        parsed.scheme != "gs"
        or parsed.params
        or parsed.query
        or parsed.fragment
        or not parsed.netloc
        or "@" in parsed.netloc
        or ":" in parsed.netloc
        or ".." in parsed.netloc
        or not _GCS_BUCKET_RE.match(parsed.netloc)
    ):
        raise ValueError("Invalid documentation URI.")
    if not parsed.path.startswith("/"):
        raise ValueError("Invalid documentation URI.")
    object_path = parsed.path[1:]
    segments = object_path.split("/")
    if not object_path or any(seg in ("", ".", "..") for seg in segments):
        raise ValueError("Invalid documentation URI.")

    allowed_buckets = get_allowed_doc_buckets()
    if not allowed_buckets or parsed.netloc not in allowed_buckets:
        raise ValueError("Documentation URI bucket is not permitted.")

    return parsed.netloc, object_path


def _clean_text_chars(text: str) -> str:
    text = _CONTROL_CHARS_RE.sub("", text)
    text = _BIDI_ZERO_WIDTH_RE.sub("", text)
    return text


def _clean_nested(value, per_item_max_chars: int | None):
    if isinstance(value, str):
        cleaned = _clean_text_chars(value)
        if per_item_max_chars is not None and len(cleaned) > per_item_max_chars:
            return cleaned[:per_item_max_chars] + "...[truncated]"
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_clean_nested(item, per_item_max_chars) for item in value]
    if isinstance(value, dict):
        return {
            _clean_nested(str(k), per_item_max_chars): _clean_nested(
                v, per_item_max_chars
            )
            for k, v in value.items()
        }
    return value


def sanitize_untrusted_prompt_data(
    value, max_chars: int = 8192, per_item_max_chars: int | None = None
) -> str:
    """Serialize, clean, truncate, and XML-escape untrusted data for prompt fencing."""
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        value = _clean_nested(value, per_item_max_chars)
        text = json.dumps(value, ensure_ascii=True, default=str)
    else:
        text = str(value)
    text = _clean_text_chars(text)
    if len(text) > max_chars:
        suffix = "...[truncated]"
        cut = max(0, max_chars - len(suffix))
        text = text[:cut] + suffix
    return html.escape(text, quote=False)


def sanitize_llm_output(text: str, max_chars: int = 8192) -> str:
    """Sanitize LLM output by stripping control chars, active HTML/scripts, and capping length."""
    if text is None:
        return ""
    cleaned = _clean_text_chars(str(text))
    while True:
        prev = cleaned
        cleaned = _DANGEROUS_HTML_BLOCKS_RE.sub("", cleaned)
        cleaned = _HTML_TAG_RE.sub("", cleaned)
        cleaned = _DANGEROUS_URI_SCHEME_RE.sub("", cleaned)
        if cleaned == prev:
            break
    cleaned = cleaned.strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars]
    return cleaned


class MetadataUtils:
    """Utility functions for metadata operations."""

    def __init__(self, client):
        """Initialize with reference to main client."""
        self._client = client

    def validate_project_id(self, project_id: str) -> str:
        return validate_project_id(project_id)

    def validate_location(self, location: str) -> str:
        return validate_location(location)

    def validate_dataset_id(self, dataset_id: str) -> str:
        return validate_dataset_id(dataset_id)

    def validate_table_id(self, table_id: str) -> str:
        return validate_table_id(table_id)

    def validate_column_name(self, column_name: str) -> str:
        return validate_column_name(column_name)

    def validate_gcs_uri(self, uri: str) -> tuple[str, str]:
        return validate_gcs_uri(uri)

    def split_table_fqn(self, table_fqn):
        """Splits a fully qualified table name into its components.

        Args:
            table_fqn (str): The fully qualified name of the table
                (e.g., 'project.dataset.table')

        Returns:
            tuple: A tuple containing (project_id, dataset_id, table_id)

        Raises:
            ValueError: If the table FQN cannot be parsed or fails validation
        """
        if not isinstance(table_fqn, str):
            raise ValueError("Invalid table FQN.")
        match = _TABLE_FQN_RE.match(table_fqn)
        if not match:
            raise ValueError("Invalid table FQN.")
        return match.group(1), match.group(2), match.group(3)

    def split_dataset_fqn(self, dataset_fqn):
        """Splits a fully qualified dataset name into its components.

        Args:
            dataset_fqn (str): The fully qualified name of the dataset
                (e.g., 'project.dataset')

        Returns:
            tuple: A tuple containing (project_id, dataset_id)

        Raises:
            ValueError: If the dataset FQN cannot be parsed or fails validation
        """
        if not isinstance(dataset_fqn, str):
            raise ValueError("Invalid dataset FQN.")
        match = _DATASET_FQN_RE.match(dataset_fqn)
        if not match:
            raise ValueError("Invalid dataset FQN.")
        return match.group(1), match.group(2)

    def combine_description(
        self,
        old_description,
        new_description,
        description_handling,
        max_length: int | None = None,
    ):
        """Combines old and new descriptions based on handling strategy."""
        old_desc = old_description or ""
        new_desc = new_description or ""
        if not new_desc:
            return old_desc[:max_length] if max_length else old_desc

        description_handling_lower = (
            description_handling.lower()
            if isinstance(description_handling, str) and description_handling
            else constants["DESCRIPTION_HANDLING"]["APPEND"]
        )

        if description_handling_lower == constants["DESCRIPTION_HANDLING"]["APPEND"]:
            if old_desc:
                try:
                    index = old_desc.index(constants["OUTPUT_CLAUSES"]["AI_WARNING"])
                    result = old_desc[:index] + new_desc
                except ValueError:
                    result = old_desc + new_desc
            else:
                result = new_desc
        elif description_handling_lower == constants["DESCRIPTION_HANDLING"]["PREPEND"]:
            result = new_desc + old_desc
        elif description_handling_lower == constants["DESCRIPTION_HANDLING"]["REPLACE"]:
            result = new_desc
        else:
            raise ValueError("Invalid description handling strategy.")

        if max_length is not None and len(result) > max_length:
            result = result[:max_length]
        return result

    def llm_inference(self, prompt, documentation_uri=None):
        """Performs LLM inference using Vertex AI.

        Args:
            prompt (str): The prompt to send to the LLM
            documentation_uri (str, optional): URI of documentation to include

        Returns:
            str: The sanitized generated text response
        """
        if isinstance(documentation_uri, str):
            documentation_uri = documentation_uri.strip() or None
        elif documentation_uri is not None:
            raise ValueError("Invalid documentation URI.")

        if documentation_uri is not None:
            if not self._client._client_options._use_ext_documents:
                raise ValueError(
                    "External documentation URI provided while use_ext_documents is disabled."
                )
            validate_gcs_uri(documentation_uri)

        max_llm_calls = get_max_llm_calls_per_request()
        if self._client._llm_calls_count >= max_llm_calls:
            raise ValueError("Maximum LLM calls per request exceeded.")
        self._client._llm_calls_count += 1

        llm_project = getattr(self._client, "_llm_project_id", self._client._project_id)
        vertexai.init(project=llm_project, location=self._client.llm_location)
        if self._client._client_options._use_ext_documents:
            model = GenerativeModel(
                constants["LLM"]["LLM_VISION_TYPE"],
                system_instruction=constants["PROMPTS"]["SYSTEM_PROMPT"],
            )
        else:
            model = GenerativeModel(
                constants["LLM"]["LLM_TYPE"],
                system_instruction=constants["PROMPTS"]["SYSTEM_PROMPT"],
            )

        generation_config = GenerationConfig(
            temperature=constants["LLM"]["TEMPERATURE"],
            top_p=constants["LLM"]["TOP_P"],
            top_k=constants["LLM"]["TOP_K"],
            candidate_count=constants["LLM"]["CANDIDATE_COUNT"],
            max_output_tokens=constants["LLM"]["MAX_OUTPUT_TOKENS"],
        )
        safety_settings = {
            generative_models.HarmCategory.HARM_CATEGORY_HATE_SPEECH: generative_models.HarmBlockThreshold.BLOCK_ONLY_HIGH,
            generative_models.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: generative_models.HarmBlockThreshold.BLOCK_ONLY_HIGH,
            generative_models.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: generative_models.HarmBlockThreshold.BLOCK_ONLY_HIGH,
            generative_models.HarmCategory.HARM_CATEGORY_HARASSMENT: generative_models.HarmBlockThreshold.BLOCK_ONLY_HIGH,
        }

        retries = 3
        base_delay = 1
        for attempt in range(retries + 1):
            try:
                if documentation_uri is not None:
                    doc = Part.from_uri(
                        documentation_uri, mime_type=constants["DATA"]["PDF_MIME_TYPE"]
                    )
                    responses = model.generate_content(
                        [doc, prompt],
                        generation_config=generation_config,
                        safety_settings=safety_settings,
                        stream=False,
                    )
                else:
                    responses = model.generate_content(
                        prompt,
                        generation_config=generation_config,
                        safety_settings=safety_settings,
                        stream=False,
                    )
                return sanitize_llm_output(
                    responses.text,
                    max_chars=constants["DATA"]["MAX_TABLE_DESC_LENGTH"],
                )
            except _RETRYABLE_EXCEPTIONS as e:
                if attempt == retries:
                    logger.error(f"Exception: {e}.")
                    raise e
                time.sleep(base_delay * (2 ** attempt))
            except Exception as e:
                logger.error(f"Exception: {e}.")
                raise e 