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
"""Dataplex Utils Metadata Wizard table operations
   2024 Google
"""
# Standard library imports
import csv
import io
import logging
import pkgutil
import random
import toml

# Cloud imports
from google.cloud import storage
from google.cloud import dataplex_v1
import google.api_core.exceptions

# Local imports
from .prompt_manager import PromtType, PromptManager
from .utils import (
    get_max_csv_bytes,
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

class TableOperations:
    """Table-specific operations."""

    def __init__(self, client):
        """Initialize with reference to main client."""
        self._client = client

    def _resolve_csv_table_fqn(self, raw_table: str, safe_dataset_fqn: str) -> str:
        if "." in raw_table:
            project_id, dataset_id, table_id = self._client._utils.split_table_fqn(raw_table)
            return f"{project_id}.{dataset_id}.{table_id}"
        self._client._utils.validate_table_id(raw_table)
        return f"{safe_dataset_fqn}.{raw_table}"

    def regenerate_dataset_tables_descriptions(self, dataset_fqn, strategy="NAIVE", documentation_csv_uri=None):
        """Regenerates metadata on the tables of a whole dataset."""
        self._client._client_options._use_human_comments = True
        self._client._client_options._regenerate = True
        return self.generate_dataset_tables_descriptions(dataset_fqn=dataset_fqn, strategy=strategy, documentation_csv_uri=documentation_csv_uri)

    def generate_dataset_tables_descriptions(self, dataset_fqn, strategy="NAIVE", documentation_csv_uri=None):
        """Generates metadata on the tables of a whole dataset."""
        project_id, dataset_id = self._client._utils.split_dataset_fqn(dataset_fqn)
        safe_dataset_fqn = f"{project_id}.{dataset_id}"
        logger.info(f"Generating metadata for dataset {safe_dataset_fqn}")
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

            max_tables = get_max_tables_per_request()
            max_llm_calls = get_max_llm_calls_per_request()
            if self._client._client_options._regenerate:
                tables = self._list_tables_in_dataset_for_regeneration(safe_dataset_fqn)
                logger.debug(f"Tables to regenerate: {len(tables)}")
            else:
                tables = self._list_tables_in_dataset(safe_dataset_fqn)
                logger.debug(f"Tables to generate: {len(tables)}")

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
                    self._list_tables_in_dataset(safe_dataset_fqn, enforce_limit=False)
                ) | regen_set
            else:
                dataset_tables = set(tables)

            def _should_regen(tbl_fqn: str) -> bool:
                return (
                    tbl_fqn in regen_set
                    or self._client._dataplex_ops.check_if_table_should_be_regenerated(tbl_fqn)
                )

            work_items = []
            if int_strategy == constants["GENERATION_STRATEGY"]["DOCUMENTED"]:
                tables_from_uri = self._get_tables_from_uri(documentation_csv_uri)
                resolved_pairs = [
                    (self._resolve_csv_table_fqn(raw_tbl, safe_dataset_fqn), doc_uri)
                    for raw_tbl, doc_uri in tables_from_uri
                ]
                for resolved_tbl, _ in resolved_pairs:
                    if resolved_tbl not in dataset_tables:
                        raise ValueError(
                            "CSV contains a table entry not found in the target dataset."
                        )
                if self._client._client_options._regenerate:
                    for resolved_tbl, doc_uri in resolved_pairs:
                        if _should_regen(resolved_tbl):
                            work_items.append((resolved_tbl, doc_uri))
                else:
                    work_items.extend(resolved_pairs)

            elif int_strategy == constants["GENERATION_STRATEGY"]["DOCUMENTED_THEN_REST"]:
                tables_from_uri = self._get_tables_from_uri(documentation_csv_uri)
                resolved_pairs = [
                    (self._resolve_csv_table_fqn(raw_tbl, safe_dataset_fqn), doc_uri)
                    for raw_tbl, doc_uri in tables_from_uri
                ]
                for resolved_tbl, _ in resolved_pairs:
                    if resolved_tbl not in dataset_tables:
                        raise ValueError(
                            "CSV contains a table entry not found in the target dataset."
                        )
                documented_set = {resolved_tbl for resolved_tbl, _ in resolved_pairs}
                if self._client._client_options._regenerate:
                    for resolved_tbl, doc_uri in resolved_pairs:
                        if _should_regen(resolved_tbl):
                            work_items.append((resolved_tbl, doc_uri))
                    for tbl in tables:
                        if tbl not in documented_set and _should_regen(tbl):
                            work_items.append((tbl, None))
                else:
                    work_items.extend(resolved_pairs)
                    for tbl in tables:
                        if tbl not in documented_set:
                            work_items.append((tbl, None))

            elif int_strategy in (
                constants["GENERATION_STRATEGY"]["NAIVE"],
                constants["GENERATION_STRATEGY"]["RANDOM"],
                constants["GENERATION_STRATEGY"]["ALPHABETICAL"],
            ):
                tables_sorted = self._order_tables_to_strategy(tables, int_strategy)
                work_items = [(tbl, None) for tbl in tables_sorted]

            if len(work_items) > max_tables:
                raise ValueError(
                    f"Dataset operation exceeds maximum tables per request ({max_tables})."
                )
            if self._client._llm_calls_count + len(work_items) > max_llm_calls:
                raise ValueError(
                    f"Dataset operation exceeds maximum LLM calls per request ({max_llm_calls})."
                )

            previews = []
            for table_fqn, doc_uri in work_items:
                result = self.generate_table_description(table_fqn, doc_uri)
                if self._client._client_options._dry_run:
                    previews.append(result)

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

    def generate_table_description(self, table_fqn, documentation_uri=None, human_comments=None):
        """Generates metadata for a table."""
        project_id, dataset_id, table_id = self._client._utils.split_table_fqn(table_fqn)
        safe_fqn = f"{project_id}.{dataset_id}.{table_id}"
        logger.info(f"Generating metadata for table {safe_fqn}.")

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
        prior_description = self._client._bigquery_ops.get_table_description(safe_fqn) or ""

        if (
            not self._client._client_options._dry_run
            and not self._client._client_options._stage_for_review
        ):
            self._client._bigquery_ops._check_overwrite_allowed(prior_description, "table")

        # Get base information
        logger.info(f"Getting schema for table {safe_fqn}.")
        table_schema_str, _ = self._client._bigquery_ops.get_table_schema(safe_fqn)
        num_rows_to_sample = constants["DATA"]["NUM_ROWS_TO_SAMPLE"]
        if num_rows_to_sample > 0:
            logger.info(f"Getting sample for table {safe_fqn}.")
            table_sample = self._client._bigquery_ops.get_table_sample(
                safe_fqn, num_rows_to_sample
            )
        else:
            table_sample = "[]"

        # Get additional information
        logger.info(f"Getting table quality for table {safe_fqn}.")
        table_quality = self._get_table_quality(
            self._client._client_options._use_data_quality, safe_fqn
        )
        logger.info(f"Getting table profile for table {safe_fqn}.")
        table_profile = self._get_table_profile(
            self._client._client_options._use_profile, safe_fqn
        )
        try:
            logger.info(f"Getting source tables for table {safe_fqn}.")
            table_sources_info = self._get_table_sources_info(
                self._client._client_options._use_lineage_tables, safe_fqn
            )
        except Exception as e:
            logger.error(f"Error getting table sources info for table {safe_fqn}: {e}")
            table_sources_info = None
        try:
            logger.info(f"Getting jobs calculating for table {safe_fqn}.")
            job_sources_info = self._get_job_sources(
                self._client._client_options._use_lineage_processes, safe_fqn
            )
        except Exception as e:
            logger.error(f"Error getting job sources info for table {safe_fqn}: {e}")
            job_sources_info = None

        # Get human comments only when explicitly enabled
        if self._client._client_options._use_human_comments:
            if human_comments is None:
                logger.info(f"Getting human comments for table {safe_fqn}.")
                human_comments = self._client._dataplex_ops.get_table_comment(safe_fqn)
        else:
            human_comments = ""

        # Get prompt
        prompt_manager = PromptManager(
            PromtType.PROMPT_TYPE_TABLE, self._client._client_options
        )
        table_description_prompt = prompt_manager.get_prompt()

        # Format prompt with sanitized, XML-escaped, bounded untrusted values
        table_description_prompt_expanded = table_description_prompt.format(
            table_fqn=sanitize_untrusted_prompt_data(safe_fqn, max_chars=512),
            table_schema_str=sanitize_untrusted_prompt_data(table_schema_str, max_chars=16384),
            table_sample=sanitize_untrusted_prompt_data(table_sample, max_chars=4096),
            table_profile=sanitize_untrusted_prompt_data(table_profile, max_chars=8192),
            table_quality=sanitize_untrusted_prompt_data(table_quality, max_chars=8192),
            table_sources_info=sanitize_untrusted_prompt_data(
                table_sources_info, max_chars=8192, per_item_max_chars=1024
            ),
            job_sources_info=sanitize_untrusted_prompt_data(
                job_sources_info, max_chars=8192, per_item_max_chars=2048
            ),
            human_comments=sanitize_untrusted_prompt_data(
                human_comments, max_chars=2048, per_item_max_chars=512
            ),
        )

        raw_description = self._client._utils.llm_inference(
            table_description_prompt_expanded, documentation_uri
        )
        table_description = sanitize_llm_output(
            raw_description, max_chars=constants["DATA"]["MAX_TABLE_DESC_LENGTH"]
        )
        if self._client._client_options._add_ai_warning:
            prefix = constants["OUTPUT_CLAUSES"]["AI_WARNING"]
            max_body = max(0, constants["DATA"]["MAX_TABLE_DESC_LENGTH"] - len(prefix))
            table_description = f"{prefix}{table_description[:max_body]}"

        if self._client._client_options._dry_run:
            combined_preview = self._client._utils.combine_description(
                prior_description,
                table_description,
                self._client._client_options._description_handling,
                max_length=constants["DATA"]["MAX_TABLE_DESC_LENGTH"],
            )
            return {
                "status": "dry_run",
                "dry_run": True,
                "table": safe_fqn,
                "previous_description": prior_description,
                "proposed_description": table_description,
                "combined_description": combined_preview,
                "staged_for_review": self._client._client_options._stage_for_review,
            }

        # Update table
        # If we are not staging for review, we update the table in BigQuery and Dataplex catalog
        if not self._client._client_options._stage_for_review:
            self._client._bigquery_ops.update_table_description(safe_fqn, table_description)
            if self._client._client_options._persist_to_dataplex_catalog:
                self._client._dataplex_ops.update_table_dataplex_description(safe_fqn, table_description)
                logger.info(f"Table description updated for table {safe_fqn} in Dataplex catalog")

        else:
            # If we are staging for review, we update the table in Dataplex catalog
            if not self._client._dataplex_ops._check_if_exists_aspect_type(constants["ASPECT_TEMPLATE"]["name"]):
                logger.info(f"Aspect type {constants['ASPECT_TEMPLATE']['name']} not exists. Attempting to create it")
                self._client._dataplex_ops._create_aspect_type(constants["ASPECT_TEMPLATE"]["name"])
                logger.info(f"Aspect type {constants['ASPECT_TEMPLATE']['name']} created")
            draft_metadata = {"external-document-uri": documentation_uri} if documentation_uri else None
            self._client._dataplex_ops.update_table_draft_description(
                safe_fqn, table_description, metadata=draft_metadata
            )
            logger.info(f"Table {safe_fqn} will not be updated in BigQuery.")
        # If we were regenerating a table, we mark it as regenerated
        if self._client._client_options._regenerate:
            self._client._dataplex_ops.mark_table_as_regenerated(safe_fqn)
            logger.info(f"Table {safe_fqn} marked as regenerated")
        return {
            "status": "success",
            "message": "Table description generated successfully",
            "details": {
                "table": safe_fqn,
                "staged_for_review": self._client._client_options._stage_for_review,
                "persisted_to_dataplex": self._client._client_options._persist_to_dataplex_catalog
            }
        }

    def _get_tables_from_uri(self, documentation_csv_uri):
        """Reads the CSV file from Google Cloud Storage and returns validated (table, doc_uri) tuples."""
        bucket_name, blob_name = validate_gcs_uri(documentation_csv_uri)
        try:
            storage_client = storage.Client(project=self._client._project_id)
            bucket = storage_client.get_bucket(bucket_name)
            blob = bucket.blob(blob_name)
            blob.reload()

            max_csv_bytes = get_max_csv_bytes()
            blob_size = getattr(blob, "size", None)
            if isinstance(blob_size, int) and blob_size > max_csv_bytes:
                raise ValueError(
                    "Documentation CSV exceeds maximum allowed size."
                )

            csv_data = blob.download_as_text(encoding="utf-8")
            if len(csv_data.encode("utf-8")) > max_csv_bytes:
                raise ValueError(
                    "Documentation CSV exceeds maximum allowed size."
                )

            max_tables = get_max_tables_per_request()
            tables = []
            reader = csv.reader(io.StringIO(csv_data))
            try:
                for row in reader:
                    if not row or all(not cell.strip() for cell in row):
                        continue
                    if len(row) != 2:
                        raise ValueError(
                            "Invalid CSV row format; expected 2 columns (table, documentation_uri)."
                        )
                    raw_table = row[0].strip()
                    doc_uri = row[1].strip()
                    if not raw_table:
                        raise ValueError("Invalid CSV row: missing table identifier.")
                    if "." in raw_table:
                        self._client._utils.split_table_fqn(raw_table)
                    else:
                        self._client._utils.validate_table_id(raw_table)

                    if doc_uri:
                        if not self._client._client_options._use_ext_documents:
                            raise ValueError(
                                "External documentation URI in CSV cannot be used when use_ext_documents is disabled."
                            )
                        validate_gcs_uri(doc_uri)
                    else:
                        doc_uri = None

                    tables.append((raw_table, doc_uri))
                    if len(tables) > max_tables:
                        raise ValueError(
                            f"Documentation CSV exceeds maximum tables per request ({max_tables})."
                        )
            except csv.Error as exc:
                raise ValueError("Invalid CSV format.") from exc

            logger.info(f"Loaded {len(tables)} table entries from documentation CSV.")
            return tables
        except Exception as e:
            logger.error(f"Exception reading documentation CSV: {e}.")
            raise e

    def _order_tables_to_strategy(self, tables, strategy):
        """Orders tables according to the specified strategy.

        Args:
            tables: List of table names
            strategy: Strategy to use for ordering

        Returns:
            Ordered list of table names
        """
        if strategy == constants["GENERATION_STRATEGY"]["NAIVE"]:
            return tables
        elif strategy == constants["GENERATION_STRATEGY"]["RANDOM"]:
            tables_copy = tables.copy()
            random.shuffle(tables_copy)
            return tables_copy
        elif strategy == constants["GENERATION_STRATEGY"]["ALPHABETICAL"]:
            return sorted(tables)
        else:
            return tables

    def _list_tables_in_dataset(self, dataset_fqn, enforce_limit: bool = True):
        """Lists tables in a given dataset (bounded by max_tables + 1 when enforce_limit=True)."""
        client = self._client._cloud_clients[constants["CLIENTS"]["BIGQUERY"]]
        project_id, dataset_id = self._client._utils.split_dataset_fqn(dataset_fqn)
        dataset_ref = client.dataset(dataset_id, project=project_id)
        max_results = (get_max_tables_per_request() + 1) if enforce_limit else None
        tables = client.list_tables(dataset_ref, max_results=max_results)
        return [str(table.full_table_id).replace(":", ".") for table in tables]

    def _list_tables_in_dataset_for_regeneration(self, dataset_fqn):
        """Lists all tables in a given dataset that need regeneration."""
        try:
            client = self._client._cloud_clients[constants["CLIENTS"]["DATAPLEX_CATALOG"]]
            project_id, dataset_id = self._client._utils.split_dataset_fqn(dataset_fqn)
            name = f"projects/{project_id}/locations/global"
            query = f"""system=BIGQUERY AND parent:{project_id}.{dataset_id} and aspect:global.{constants['ASPECT_TEMPLATE']['name']}.to-be-regenerated=true"""
            logger.info(f"Query: {query}")

            max_tables = get_max_tables_per_request()
            request = dataplex_v1.SearchEntriesRequest(
                name=name,
                query=query,
                page_size=min(max_tables + 1, 100),
            )

            table_names = []
            search_results = client.search_entries(request=request)
            for result in search_results:
                if result.dataplex_entry.fully_qualified_name.startswith("bigquery:"):
                    table_fqn = result.dataplex_entry.fully_qualified_name.replace("bigquery:", "")
                    table_names.append(table_fqn)
                    if len(table_names) > max_tables:
                        break
            return table_names

        except Exception as e:
            logger.error(f"Error listing tables in dataset {dataset_fqn}: {e}")
            raise e 

    def _get_table_quality(self, use_data_quality, table_fqn):
        """Gets the quality information for a table.

        Args:
            use_data_quality (bool): Whether to use data quality information
            table_fqn (str): The fully qualified name of the table

        Returns:
            dict: Table quality information or None if not available/enabled
        """
        if not use_data_quality:
            return None
        return self._client._dataplex_ops.get_table_quality(use_data_quality, table_fqn)

    def _get_table_profile(self, use_profile, table_fqn):
        """Gets the profile information for a table.

        Args:
            use_profile (bool): Whether to use profile information
            table_fqn (str): The fully qualified name of the table

        Returns:
            dict: Table profile information or None if not available/enabled
        """
        if not use_profile:
            return None
        return self._client._dataplex_ops.get_table_profile(use_profile, table_fqn)

    def _get_table_sources_info(self, use_lineage_tables, table_fqn):
        """Gets the source table information.

        Args:
            use_lineage_tables (bool): Whether to use lineage table information
            table_fqn (str): The fully qualified name of the table

        Returns:
            dict: Source table information or None if not available/enabled
        """
        if not use_lineage_tables:
            return None
        return self._client._dataplex_ops.get_table_sources_info(use_lineage_tables, table_fqn)

    def _get_job_sources(self, use_lineage_processes, table_fqn):
        """Gets the job source information.

        Args:
            use_lineage_processes (bool): Whether to use lineage process information
            table_fqn (str): The fully qualified name of the table

        Returns:
            dict: Job source information or None if not available/enabled
        """
        if not use_lineage_processes:
            return None
        return self._client._dataplex_ops.get_job_sources(use_lineage_processes, table_fqn) 