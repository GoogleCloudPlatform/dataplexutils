#!/usr/bin/env python
# -*- coding: utf-8 -*-
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
"""CLI interface to metadata wizard API
   2024 Google
"""

# Standard library imports
import argparse
import logging
import os

# Third-party imports
import requests

# Setup logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def str_to_bool(value: str | bool) -> bool:
    """Convert CLI boolean string arguments accurately."""
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y", "on"}:
        return True
    if normalized in {"false", "0", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Boolean value expected, got {value!r}")


def _get_iam_id_token(audience: str) -> str:
    """Fetch a Google OIDC ID token for Cloud Run IAM authentication."""
    from google.auth.transport import requests as google_auth_requests
    from google.oauth2 import id_token as google_id_token

    auth_req = google_auth_requests.Request()
    return google_id_token.fetch_id_token(auth_req, audience)


def _build_auth_headers(
    api_base_url: str, api_token: str | None = None, use_iam_auth: bool = False
) -> dict[str, str]:
    """Build authentication headers from API token / env var and optional IAM OIDC."""
    headers: dict[str, str] = {}
    resolved_token = (
        api_token if api_token is not None else os.environ.get("METADATA_WIZARD_API_TOKEN", "")
    ).strip()
    if resolved_token:
        headers["X-API-Key"] = resolved_token

    if use_iam_auth:
        oidc_token = _get_iam_id_token(api_base_url)
        headers["Authorization"] = f"Bearer {oidc_token}"

    return headers


def _call_api(
    service,
    scope,
    use_lineage_tables,
    use_lineage_processes,
    use_profile,
    use_data_quality,
    use_ext_documents,
    persist_to_dataplex_catalog,
    stage_for_review,
    dataplex_project_id,
    llm_location,
    dataplex_location,
    documentation_uri,
    table_project_id,
    table_dataset_id,
    table_id,
    debug,
    documentation_csv_uri,
    strategy,
    top_values_in_description,
    description_handling="APPEND",
    description_prefix="",
    regeneration_filter="",
    table_fqn="",
    column_name="",
    dry_run=False,
    confirm_overwrite=False,
    api_token=None,
    use_iam_auth=False,
):
    """Call the metadata wizard API with the provided parameters."""
    if service.startswith(("http://", "https://")):
        API_URL = service.rstrip("/")
    else:
        API_URL = f"https://{service}"
    API_URL_DEBUG = "http://localhost:8000"
    METADATA_TABLE_SCOPE_ROUTE = "/generate_table_description"
    METADATA_COLUMNS_SCOPE_ROUTE = "/generate_columns_descriptions"
    METADATA_DATASET_SCOPE_ROUTE = "/generate_dataset_tables_descriptions"
    METADATA_DATASET_COLUMNS_SCOPE_ROUTE = "/generate_dataset_tables_columns_descriptions"
    REGENERATE_ALL_ROUTE = "/regenerate_all"
    REGENERATE_SELECTED_ROUTE = "/regenerate_selected"
    GET_REGENERATION_COUNTS_ROUTE = "/get_regeneration_counts"
    MARK_FOR_REGENERATION_ROUTE = "/mark_for_regeneration"

    if debug:
        API_URL = API_URL_DEBUG

    if scope == "table":
        url = API_URL + METADATA_TABLE_SCOPE_ROUTE
    elif scope == "columns":
        url = API_URL + METADATA_COLUMNS_SCOPE_ROUTE
    elif scope == "dataset":
        url = API_URL + METADATA_DATASET_SCOPE_ROUTE
    elif scope == "dataset_columns":
        url = API_URL + METADATA_DATASET_COLUMNS_SCOPE_ROUTE
    elif scope == "regenerate_all":
        url = API_URL + REGENERATE_ALL_ROUTE
    elif scope == "regenerate_selected":
        url = API_URL + REGENERATE_SELECTED_ROUTE
    elif scope == "get_regeneration_counts":
        url = API_URL + GET_REGENERATION_COUNTS_ROUTE
    elif scope == "mark_for_regeneration":
        url = API_URL + MARK_FOR_REGENERATION_ROUTE
    else:
        raise ValueError(f"Invalid scope: {scope}")

    client_options_payload = {
        "use_lineage_tables": use_lineage_tables,
        "use_lineage_processes": use_lineage_processes,
        "use_profile": use_profile,
        "use_data_quality": use_data_quality,
        "use_ext_documents": use_ext_documents,
        "persist_to_dataplex_catalog": persist_to_dataplex_catalog,
        "stage_for_review": stage_for_review,
        "top_values_in_description": top_values_in_description,
        "description_handling": description_handling,
        "description_prefix": description_prefix,
        "dry_run": dry_run,
        "confirm_overwrite": confirm_overwrite,
    }

    if scope in ["regenerate_all", "regenerate_selected"]:
        params = {
            "client_options_settings": client_options_payload,
            "client_settings": {
                "project_id": dataplex_project_id,
                "llm_location": llm_location,
                "dataplex_location": dataplex_location,
            },
            "dataset_settings": {
                "project_id": table_project_id,
                "dataset_id": table_dataset_id,
                "documentation_csv_uri": documentation_csv_uri,
                "strategy": strategy,
            },
        }
        if scope == "regenerate_selected":
            params["regeneration_request"] = {
                "objects": [regeneration_filter] if regeneration_filter else []
            }

    elif scope == "get_regeneration_counts":
        params = {
            "client_settings": {
                "project_id": dataplex_project_id,
                "llm_location": llm_location,
                "dataplex_location": dataplex_location,
            },
            "dataset_settings": {
                "project_id": table_project_id,
                "dataset_id": table_dataset_id,
                "documentation_csv_uri": documentation_csv_uri,
                "strategy": strategy,
            },
        }

    elif scope == "mark_for_regeneration":
        params = {
            "client_settings": {
                "project_id": dataplex_project_id,
                "llm_location": llm_location,
                "dataplex_location": dataplex_location,
            },
            "request": {
                "table_fqn": table_fqn,
                "column_name": column_name if column_name else None,
            },
        }

    else:
        params = {
            "client_options_settings": client_options_payload,
            "client_settings": {
                "project_id": dataplex_project_id,
                "llm_location": llm_location,
                "dataplex_location": dataplex_location,
            },
            "table_settings": {
                "project_id": table_project_id,
                "dataset_id": table_dataset_id,
                "table_id": table_id,
                "documentation_uri": documentation_uri,
            },
            "dataset_settings": {
                "project_id": table_project_id,
                "dataset_id": table_dataset_id,
                "documentation_csv_uri": documentation_csv_uri,
                "strategy": strategy,
            },
        }

    try:
        headers = _build_auth_headers(
            api_base_url=API_URL,
            api_token=api_token,
            use_iam_auth=use_iam_auth,
        )
        logger.debug("Sending POST request to %s", url)
        response = requests.post(url, json=params, headers=headers, timeout=120)
        response.raise_for_status()
        result = response.json()
        logger.debug("Received response: %s", result)
        print(result)
        return result
    except requests.exceptions.RequestException as e:
        logger.error("Error calling API: %s", e)
        print(f"Error calling API: {e}")
        return None
    except requests.exceptions.JSONDecodeError as e:
        logger.error("Error decoding JSON response: %s", e)
        print(f"Error decoding JSON response: {e}")
        return None


def _get_input_arguments():
    """Parse command line arguments.

    Returns:
        argparse.Namespace: The parsed command line arguments
    """
    parser = argparse.ArgumentParser(description="Call Metadata Wizard API.")

    # Required arguments
    parser.add_argument(
        "--service",
        dest="service",
        required=True,
        type=str,
        help="The API service endpoint",
    )
    parser.add_argument(
        "--scope",
        dest="scope",
        required=True,
        type=str,
        help="The scope of the operation (table, columns, dataset, dataset_columns, regenerate_all, regenerate_selected, get_regeneration_counts, mark_for_regeneration)",
    )
    parser.add_argument(
        "--dataplex_project_id",
        dest="dataplex_project_id",
        required=True,
        type=str,
        help="The Dataplex project ID",
    )
    parser.add_argument(
        "--llm_location",
        dest="llm_location",
        required=True,
        type=str,
        help="The LLM location",
    )
    parser.add_argument(
        "--dataplex_location",
        dest="dataplex_location",
        required=True,
        type=str,
        help="The Dataplex location",
    )
    parser.add_argument(
        "--table_project_id",
        dest="table_project_id",
        required=True,
        type=str,
        help="The table project ID",
    )
    parser.add_argument(
        "--table_dataset_id",
        dest="table_dataset_id",
        required=True,
        type=str,
        help="The table dataset ID",
    )
    parser.add_argument(
        "--table_id",
        dest="table_id",
        required=False,
        type=str,
        default="",
        help="The table ID (required for table and columns scopes)",
    )

    # Optional arguments with defaults
    parser.add_argument(
        "--use_lineage_tables",
        dest="use_lineage_tables",
        required=False,
        default=False,
        type=str_to_bool,
        help="Whether to use lineage tables",
    )
    parser.add_argument(
        "--use_lineage_processes",
        dest="use_lineage_processes",
        required=False,
        default=False,
        type=str_to_bool,
        help="Whether to use lineage processes",
    )
    parser.add_argument(
        "--use_profile",
        dest="use_profile",
        required=False,
        default=False,
        type=str_to_bool,
        help="Whether to use profile information",
    )
    parser.add_argument(
        "--use_data_quality",
        dest="use_data_quality",
        required=False,
        default=False,
        type=str_to_bool,
        help="Whether to use data quality information",
    )
    parser.add_argument(
        "--use_ext_documents",
        dest="use_ext_documents",
        required=False,
        default=False,
        type=str_to_bool,
        help="Whether to use external documents",
    )
    parser.add_argument(
        "--documentation_uri",
        dest="documentation_uri",
        required=False,
        default="",
        type=str,
        help="The documentation URI",
    )
    parser.add_argument(
        "--debug",
        dest="debug",
        required=False,
        type=str_to_bool,
        default=False,
        help="Whether to use debug mode",
    )
    parser.add_argument(
        "--strategy",
        dest="strategy",
        required=False,
        type=str,
        default="NAIVE",
        help="The generation strategy",
    )
    parser.add_argument(
        "--documentation_csv_uri",
        dest="documentation_csv_uri",
        required=False,
        type=str,
        default="",
        help="The documentation CSV URI",
    )
    parser.add_argument(
        "--persist_to_dataplex_catalog",
        dest="persist_to_dataplex_catalog",
        required=False,
        type=str_to_bool,
        default=False,
        help="Whether to persist to Dataplex catalog",
    )
    parser.add_argument(
        "--stage_for_review",
        dest="stage_for_review",
        required=False,
        type=str_to_bool,
        default=True,
        help="Whether to stage generated descriptions for review (default: True)",
    )
    parser.add_argument(
        "--dry_run",
        dest="dry_run",
        required=False,
        type=str_to_bool,
        default=False,
        help="Preview proposed descriptions without writing to BigQuery or Dataplex",
    )
    parser.add_argument(
        "--confirm_overwrite",
        dest="confirm_overwrite",
        required=False,
        type=str_to_bool,
        default=False,
        help="Explicitly confirm overwriting human-curated descriptions under replace mode",
    )
    parser.add_argument(
        "--top_values_in_description",
        dest="top_values_in_description",
        required=False,
        type=str_to_bool,
        default=True,
        help="Whether to include top values in description",
    )
    parser.add_argument(
        "--description_handling",
        dest="description_handling",
        required=False,
        type=str,
        default="APPEND",
        help="How to handle description updates",
    )
    parser.add_argument(
        "--description_prefix",
        dest="description_prefix",
        required=False,
        type=str,
        default="",
        help="Prefix for generated descriptions",
    )
    parser.add_argument(
        "--regeneration_filter",
        dest="regeneration_filter",
        required=False,
        type=str,
        default="",
        help="Filter pattern for selective regeneration",
    )
    parser.add_argument(
        "--table_fqn",
        dest="table_fqn",
        required=False,
        type=str,
        default="",
        help="Fully qualified table name for marking regeneration",
    )
    parser.add_argument(
        "--column_name",
        dest="column_name",
        required=False,
        type=str,
        default="",
        help="Column name for marking specific column for regeneration",
    )
    parser.add_argument(
        "--api_token",
        dest="api_token",
        required=False,
        type=str,
        default=None,
        help="API token sent as X-API-Key (defaults to METADATA_WIZARD_API_TOKEN env var)",
    )
    parser.add_argument(
        "--use_iam_auth",
        dest="use_iam_auth",
        required=False,
        type=str_to_bool,
        default=False,
        help="Fetch a Google OIDC ID token for Cloud Run IAM authentication",
    )

    args = parser.parse_args()

    # Validate scope-specific requirements
    if args.scope in ["table", "columns"] and not args.table_id:
        parser.error(f"--table_id is required for scope '{args.scope}'")

    if args.scope == "mark_for_regeneration" and not args.table_fqn:
        parser.error(f"--table_fqn is required for scope '{args.scope}'")

    return args


def main():
    """Main entry point for the CLI."""
    args = _get_input_arguments()
    _call_api(
        service=args.service,
        scope=args.scope,
        use_lineage_tables=args.use_lineage_tables,
        use_lineage_processes=args.use_lineage_processes,
        use_profile=args.use_profile,
        use_data_quality=args.use_data_quality,
        use_ext_documents=args.use_ext_documents,
        persist_to_dataplex_catalog=args.persist_to_dataplex_catalog,
        stage_for_review=args.stage_for_review,
        dataplex_project_id=args.dataplex_project_id,
        llm_location=args.llm_location,
        dataplex_location=args.dataplex_location,
        documentation_uri=args.documentation_uri,
        table_project_id=args.table_project_id,
        table_dataset_id=args.table_dataset_id,
        table_id=args.table_id,
        debug=args.debug,
        documentation_csv_uri=args.documentation_csv_uri,
        strategy=args.strategy,
        top_values_in_description=args.top_values_in_description,
        description_handling=args.description_handling,
        description_prefix=args.description_prefix,
        regeneration_filter=args.regeneration_filter,
        table_fqn=args.table_fqn,
        column_name=args.column_name,
        dry_run=args.dry_run,
        confirm_overwrite=args.confirm_overwrite,
        api_token=args.api_token,
        use_iam_auth=args.use_iam_auth,
    )


if __name__ == "__main__":
    main()
