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
"""Dataplex Utils Metadata Wizard column operations
   2024 Google
"""
# Standard library imports
import logging
import toml
import pkgutil

# Cloud imports
from google.cloud import bigquery

# Local imports
from .prompt_manager import PromtType, PromptManager
from .utils import (
    get_max_columns_per_request,
    get_max_llm_calls_per_request,
    get_max_tables_per_request,
    sanitize_llm_output,
    sanitize_untrusted_prompt_data,
    validate_gcs_uri,
)

# Load constants
constants = toml.loads(pkgutil.get_data(__name__, "constants.toml").decode())
# Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(constants["LOGGING"]["WIZARD_LOGGER"])

class ColumnOperations:
    """Column-specific operations."""

    def __init__(self, client):
        """Initialize with reference to main client."""
        self._client = client

    def regenerate_dataset_tables_columns_descriptions(self, dataset_fqn, strategy="NAIVE", documentation_csv_uri=None):
        """Regenerates metadata on the columns of tables in a whole dataset."""
        try:
            self._client._client_options._use_human_comments = True
            self._client._client_options._regenerate = True
            return self.generate_dataset_tables_columns_descriptions(dataset_fqn, strategy, documentation_csv_uri)
        except Exception as e:
            logger.error(f"Exception: {e}.")
            raise e

    def generate_dataset_tables_columns_descriptions(self, dataset_fqn, strategy="NAIVE", documentation_csv_uri=None):
        """Generates metadata on the columns of tables in a whole dataset."""
        project_id, dataset_id = self._client._utils.split_dataset_fqn(dataset_fqn)
        safe_dataset_fqn = f"{project_id}.{dataset_id}"
        logger.info(f"Generating column metadata for dataset {safe_dataset_fqn}.")
        try:
            if strategy not in constants["GENERATION_STRATEGY"]:
                raise ValueError(
                    f"Invalid strategy. Valid strategies are: {list(constants['GENERATION_STRATEGY'].keys())}"
                )

            int_strategy = constants["GENERATION_STRATEGY"][strategy]
            if int_strategy not in constants["GENERATION_STRATEGY"].values():
                raise ValueError("Invalid strategy.")

            if int_strategy in (
                constants["GENERATION_STRATEGY"]["DOCUMENTED"],
                constants["GENERATION_STRATEGY"]["DOCUMENTED_THEN_REST"],
            ):
                if not documentation_csv_uri or not str(documentation_csv_uri).strip():
                    raise ValueError(
                        "A documentation CSV URI is required for the selected strategy."
                    )

            if self._client._client_options._regenerate:
                tables = self._client._table_ops._list_tables_in_dataset_for_regeneration(safe_dataset_fqn)
                logger.debug(f"Tables to regenerate columns: {len(tables)}")
            else:
                tables = self._client._table_ops._list_tables_in_dataset(safe_dataset_fqn)
                logger.debug(f"Tables to generate columns: {len(tables)}")

            max_tables = get_max_tables_per_request()
            max_columns = get_max_columns_per_request()
            max_llm_calls = get_max_llm_calls_per_request()
            if len(tables) > max_tables:
                raise ValueError(
                    f"Dataset exceeds maximum tables per request ({max_tables})."
                )

            regen_set = set(tables) if self._client._client_options._regenerate else set()
            if (
                self._client._client_options._regenerate
                and int_strategy in (
                    constants["GENERATION_STRATEGY"]["DOCUMENTED"],
                    constants["GENERATION_STRATEGY"]["DOCUMENTED_THEN_REST"],
                )
            ):
                dataset_tables = set(
                    self._client._table_ops._list_tables_in_dataset(
                        safe_dataset_fqn, enforce_limit=False
                    )
                ) | regen_set
            else:
                dataset_tables = set(tables)

            def _should_gen_table(tbl_fqn: str) -> bool:
                if not self._client._client_options._regenerate:
                    return True
                return self._client._dataplex_ops.check_if_table_should_be_regenerated(tbl_fqn)

            # Build list of (table_fqn, doc_uri, include_table_description)
            work_items = []
            if int_strategy == constants["GENERATION_STRATEGY"]["DOCUMENTED"]:
                tables_from_uri = self._client._table_ops._get_tables_from_uri(documentation_csv_uri)
                for raw_tbl, doc_uri in tables_from_uri:
                    resolved_tbl = self._client._table_ops._resolve_csv_table_fqn(
                        raw_tbl, safe_dataset_fqn
                    )
                    if resolved_tbl not in dataset_tables:
                        raise ValueError(
                            "CSV contains a table entry not found in the target dataset."
                        )
                    if not self._client._client_options._regenerate or resolved_tbl in regen_set:
                        work_items.append((resolved_tbl, doc_uri, False))

            elif int_strategy == constants["GENERATION_STRATEGY"]["DOCUMENTED_THEN_REST"]:
                tables_from_uri = self._client._table_ops._get_tables_from_uri(documentation_csv_uri)
                documented_set = set()
                for raw_tbl, doc_uri in tables_from_uri:
                    resolved_tbl = self._client._table_ops._resolve_csv_table_fqn(
                        raw_tbl, safe_dataset_fqn
                    )
                    if resolved_tbl not in dataset_tables:
                        raise ValueError(
                            "CSV contains a table entry not found in the target dataset."
                        )
                    documented_set.add(resolved_tbl)
                    if not self._client._client_options._regenerate or resolved_tbl in regen_set:
                        work_items.append((resolved_tbl, doc_uri, _should_gen_table(resolved_tbl)))
                for tbl in tables:
                    if tbl not in documented_set:
                        work_items.append((tbl, None, _should_gen_table(tbl)))

            elif int_strategy in (
                constants["GENERATION_STRATEGY"]["NAIVE"],
                constants["GENERATION_STRATEGY"]["RANDOM"],
                constants["GENERATION_STRATEGY"]["ALPHABETICAL"],
            ):
                tables_sorted = self._client._table_ops._order_tables_to_strategy(tables, int_strategy)
                work_items = [(tbl, None, _should_gen_table(tbl)) for tbl in tables_sorted]

            if len(work_items) > max_tables:
                raise ValueError(
                    f"Dataset operation exceeds maximum tables per request ({max_tables})."
                )

            # Preflight total column + table LLM call budget across all tables before any LLM call/mutation
            total_planned_calls = 0
            for tbl_fqn, _, gen_table in work_items:
                self._client._bigquery_ops.table_exists(tbl_fqn)
                _, tbl_schema = self._client._bigquery_ops.get_table_schema(tbl_fqn)
                if len(tbl_schema) > max_columns:
                    raise ValueError(
                        f"Table exceeds maximum columns per request ({max_columns})."
                    )
                if self._client._client_options._regenerate:
                    cols_planned = sum(
                        1
                        for col in tbl_schema
                        if self._client._dataplex_ops.check_if_column_should_be_regenerated(
                            tbl_fqn, col.name
                        )
                    )
                else:
                    cols_planned = len(tbl_schema)
                total_planned_calls += cols_planned + (1 if gen_table else 0)

            if self._client._llm_calls_count + total_planned_calls > max_llm_calls:
                raise ValueError(
                    f"Dataset operation exceeds maximum LLM calls per request ({max_llm_calls})."
                )

            previews = []
            for tbl_fqn, doc_uri, gen_table in work_items:
                cols_res = self.generate_columns_descriptions(tbl_fqn, doc_uri)
                tbl_res = None
                if gen_table:
                    tbl_res = self._client._table_ops.generate_table_description(tbl_fqn, doc_uri)
                if self._client._client_options._dry_run:
                    previews.append(
                        {
                            "table": tbl_fqn,
                            "columns_preview": cols_res,
                            "table_preview": tbl_res,
                        }
                    )

            if self._client._client_options._dry_run:
                return {
                    "status": "dry_run",
                    "dry_run": True,
                    "dataset": safe_dataset_fqn,
                    "tables": previews,
                }

        except Exception as e:
            logger.error(f"Exception: {e}.")
            raise e

    def generate_columns_descriptions(self, table_fqn, documentation_uri=None, human_comments=None):
        """Generates metadata on the columns."""
        try:
            project_id, dataset_id, table_id = self._client._utils.split_table_fqn(table_fqn)
            safe_fqn = f"{project_id}.{dataset_id}.{table_id}"
            logger.info(f"Generating metadata for columns in table {safe_fqn}.")

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

            self._client._bigquery_ops.table_exists(safe_fqn)
            table_schema_str, table_schema = self._client._bigquery_ops.get_table_schema(safe_fqn)

            max_columns = get_max_columns_per_request()
            if len(table_schema) > max_columns:
                raise ValueError(
                    f"Table exceeds maximum columns per request ({max_columns})."
                )

            columns_to_generate = []
            for column in table_schema:
                self._client._utils.validate_column_name(column.name)
                if (
                    not self._client._client_options._regenerate
                    or self._client._dataplex_ops.check_if_column_should_be_regenerated(
                        safe_fqn, column.name
                    )
                ):
                    columns_to_generate.append(column.name)

            max_llm_calls = get_max_llm_calls_per_request()
            if self._client._llm_calls_count + len(columns_to_generate) > max_llm_calls:
                raise ValueError(
                    f"Operation exceeds maximum LLM calls per request ({max_llm_calls})."
                )
            columns_to_generate_set = set(columns_to_generate)

            if (
                not self._client._client_options._dry_run
                and not self._client._client_options._stage_for_review
            ):
                for column in table_schema:
                    if column.name in columns_to_generate_set:
                        self._client._bigquery_ops._check_overwrite_allowed(
                            getattr(column, "description", None) or "",
                            f"column '{column.name}'",
                        )

            num_rows_to_sample = constants["DATA"]["NUM_ROWS_TO_SAMPLE"]
            if num_rows_to_sample > 0:
                table_sample = self._client._bigquery_ops.get_table_sample(
                    safe_fqn, num_rows_to_sample
                )
            else:
                table_sample = "[]"

            # Get additional information
            table_quality = self._client._dataplex_ops.get_table_quality(
                self._client._client_options._use_data_quality, safe_fqn
            )
            table_profile = self._client._dataplex_ops.get_table_profile(
                self._client._client_options._use_profile, safe_fqn
            )
            try:
                table_sources_info = self._client._dataplex_ops.get_table_sources_info(
                    self._client._client_options._use_lineage_tables, safe_fqn
                )
            except Exception as e:
                logger.error(f"Error getting table sources info for table {safe_fqn}: {e}")
                table_sources_info = None
            try:
                job_sources_info = self._client._dataplex_ops.get_job_sources(
                    self._client._client_options._use_lineage_processes, safe_fqn
                )
            except Exception as e:
                logger.error(f"Error getting job sources info for table {safe_fqn}: {e}")
                job_sources_info = None

            prompt_manager = PromptManager(
                PromtType.PROMPT_TYPE_COLUMN, self._client._client_options
            )
            column_description_prompt = prompt_manager.get_prompt()

            updated_schema = []
            updated_columns = []
            dry_run_columns = []

            safe_schema_str = sanitize_untrusted_prompt_data(table_schema_str, max_chars=16384)
            safe_sample = sanitize_untrusted_prompt_data(table_sample, max_chars=4096)
            safe_quality = sanitize_untrusted_prompt_data(table_quality, max_chars=8192)
            safe_sources = sanitize_untrusted_prompt_data(
                table_sources_info, max_chars=8192, per_item_max_chars=1024
            )
            safe_jobs = sanitize_untrusted_prompt_data(
                job_sources_info, max_chars=8192, per_item_max_chars=2048
            )

            # Iterate over the columns in the table schema
            for column in table_schema:
                if column.name not in columns_to_generate_set:
                    updated_schema.append(column)
                    if self._client._client_options._dry_run:
                        dry_run_columns.append(
                            {
                                "column": column.name,
                                "previous_description": getattr(column, "description", "") or "",
                                "proposed_description": None,
                                "combined_description": getattr(column, "description", "") or "",
                                "skipped": True,
                            }
                        )
                    logger.info(f"Column {column.name} will not be updated.")
                    continue

                column_info = self._extract_column_info_from_table_profile(table_profile, column.name)

                # Reset per column to avoid cross-column bleed
                col_human_comments = ""
                if self._client._client_options._use_human_comments:
                    col_human_comments = self._client._dataplex_ops.get_column_comment(
                        safe_fqn, column.name
                    )

                column_description_prompt_expanded = column_description_prompt.format(
                    column_name=sanitize_untrusted_prompt_data(column.name, max_chars=300),
                    table_fqn=sanitize_untrusted_prompt_data(safe_fqn, max_chars=512),
                    table_schema_str=safe_schema_str,
                    table_sample=safe_sample,
                    table_profile=sanitize_untrusted_prompt_data(column_info, max_chars=8192),
                    table_quality=safe_quality,
                    table_sources_info=safe_sources,
                    job_sources_info=safe_jobs,
                    human_comments=sanitize_untrusted_prompt_data(
                        col_human_comments, max_chars=2048, per_item_max_chars=512
                    ),
                )

                raw_col_desc = self._client._utils.llm_inference(
                    column_description_prompt_expanded,
                    documentation_uri=documentation_uri,
                )
                column_description = sanitize_llm_output(
                    raw_col_desc, max_chars=constants["DATA"]["MAX_COLUMN_DESC_LENGTH"]
                )
                if self._client._client_options._add_ai_warning:
                    prefix = constants["OUTPUT_CLAUSES"]["AI_WARNING"]
                    max_body = max(0, constants["DATA"]["MAX_COLUMN_DESC_LENGTH"] - len(prefix))
                    column_description = f"{prefix}{column_description[:max_body]}"

                if self._client._client_options._dry_run:
                    prior_col_desc = getattr(column, "description", None) or ""
                    combined_preview = self._client._utils.combine_description(
                        prior_col_desc,
                        column_description,
                        self._client._client_options._description_handling,
                        max_length=constants["DATA"]["MAX_COLUMN_DESC_LENGTH"],
                    )
                    dry_run_columns.append(
                        {
                            "column": column.name,
                            "previous_description": prior_col_desc,
                            "proposed_description": column_description,
                            "combined_description": combined_preview,
                            "skipped": False,
                        }
                    )
                    continue

                if self._client._client_options._stage_for_review:
                    if not self._client._dataplex_ops._check_if_exists_aspect_type(
                        constants["ASPECT_TEMPLATE"]["name"]
                    ):
                        self._client._dataplex_ops._create_aspect_type(
                            constants["ASPECT_TEMPLATE"]["name"]
                        )
                    self._client._dataplex_ops.update_column_draft_description(
                        safe_fqn, column.name, column_description
                    )
                else:
                    updated_field = self._get_updated_column(column, column_description)
                    updated_schema.append(updated_field)
                updated_columns.append(column)
                logger.info(f"Generated column description for {column.name}.")

            if self._client._client_options._dry_run:
                return {
                    "status": "dry_run",
                    "dry_run": True,
                    "table": safe_fqn,
                    "columns": dry_run_columns,
                }

            if not self._client._client_options._stage_for_review:
                self._client._bigquery_ops.update_table_schema(safe_fqn, updated_schema)

            if self._client._client_options._regenerate:
                for column in updated_columns:
                    self._client._dataplex_ops.mark_column_as_regenerated(safe_fqn, column.name)
                    logger.info(f"Marked table {safe_fqn} column {column.name} as regenerated")

        except Exception as e:
            logger.error(f"Update of column description table {table_fqn} failed.")
            raise e

    def regenerate_columns_descriptions(self, table_fqn, documentation_uri=None, human_comments=None):
        """Regenerate columns descriptions.

        Args:
            table_fqn: The fully qualified name of the table
            documentation_uri: Optional URI to documentation
            human_comments: Optional human comments to consider

        Returns:
            None.

        Raises:
            Exception: If there is an error regenerating descriptions
        """
        self._client._client_options._use_human_comments = True
        self._client._client_options._regenerate = True
        try:
            output = self.generate_columns_descriptions(table_fqn, documentation_uri, human_comments)
            return output
        except Exception as e:
            logger.error(f"Exception: {e}.")
            raise e

    def _extract_column_info_from_table_profile(self, profile, column_name):
        """Extract profile information for a specific column from the table profile.
        
        Args:
            profile (list): The table profile information
            column_name (str): Name of the column to extract information for
            
        Returns:
            dict: Dictionary containing column profile information or None if column not found
        """
        try:
            if not profile or len(profile) == 0:
                logger.info(f"No profile found for column {column_name}.")
                return None
            
            fields = profile[0]['profile']['fields']
            
            # Find the matching column
            for field in fields:
                if field['name'] == column_name:
                    column_info = {
                        'name': field['name'],
                        'type': field['type'],
                        'mode': field['mode'],
                        'null_ratio': field['profile'].get('nullRatio', 0),
                        'distinct_ratio': field['profile'].get('distinctRatio', 0),
                    }
                    
                    # Add type-specific profile information
                    if 'integerProfile' in field['profile']:
                        column_info.update({
                            'average': field['profile']['integerProfile'].get('average'),
                            'std_dev': field['profile']['integerProfile'].get('standardDeviation'),
                            'min': field['profile']['integerProfile'].get('min'),
                            'max': field['profile']['integerProfile'].get('max'),
                            'quartiles': field['profile']['integerProfile'].get('quartiles')
                        })
                    elif 'stringProfile' in field['profile']:
                        column_info.update({
                            'min_length': field['profile']['stringProfile'].get('minLength'),
                            'max_length': field['profile']['stringProfile'].get('maxLength'),
                            'avg_length': field['profile']['stringProfile'].get('averageLength')
                        })
                    elif 'doubleProfile' in field['profile']:
                        column_info.update({
                            'average': field['profile']['doubleProfile'].get('average'),
                            'std_dev': field['profile']['doubleProfile'].get('standardDeviation'),
                            'min': field['profile']['doubleProfile'].get('min'),
                            'max': field['profile']['doubleProfile'].get('max'),
                            'quartiles': field['profile']['doubleProfile'].get('quartiles')
                        })
                    
                    # Add top N values if available
                    if 'topNValues' in field['profile']:
                        column_info['top_values'] = field['profile']['topNValues']
                    
                    return column_info
                    
            return None
            
        except Exception as e:
            logger.error(f"Error extracting column info: {str(e)}")
            return None

    def _get_updated_column(self, column, column_description):
        """Creates an updated column schema field with new description."""
        try:
            existing_description = getattr(column, "description", None) or ""
            self._client._bigquery_ops._check_overwrite_allowed(
                existing_description, f"column '{column.name}'"
            )

            sanitized_new = sanitize_llm_output(
                column_description,
                max_chars=constants["DATA"]["MAX_COLUMN_DESC_LENGTH"],
            )
            combined_description = self._client._utils.combine_description(
                existing_description,
                sanitized_new,
                self._client._client_options._description_handling,
                max_length=constants["DATA"]["MAX_COLUMN_DESC_LENGTH"],
            )

            extra_kwargs = {
                attr: getattr(column, attr)
                for attr in (
                    "default_value_expression",
                    "precision",
                    "scale",
                    "max_length",
                    "range_element_type",
                )
                if getattr(column, attr, None) is not None
            }
            return bigquery.SchemaField(
                name=column.name,
                field_type=column.field_type,
                mode=column.mode,
                description=combined_description,
                fields=column.fields,
                policy_tags=column.policy_tags,
                **extra_kwargs,
            )
        except Exception as e:
            logger.error(f"Exception: {e}.")
            raise e 