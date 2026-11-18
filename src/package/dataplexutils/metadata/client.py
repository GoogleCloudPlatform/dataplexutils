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
"""Dataplex Utils Metadata Wizard main client
   2024 Google
"""
from .version import __version__

# Standard library imports
import logging
import os
import toml
import pkgutil

# Cloud imports
from google.cloud import bigquery
from google.cloud import dataplex_v1
from google.cloud import datacatalog_lineage_v1

# Local imports
from .prompt_manager import PromtType, PromptManager
from .client_options import ClientOptions
from .table_operations import TableOperations
from .column_operations import ColumnOperations
from .dataplex_operations import DataplexOperations
from .bigquery_operations import BigQueryOperations
from .review_operations import ReviewOperations
from .utils import MetadataUtils

# Load constants
constants = toml.loads(pkgutil.get_data(__name__, "constants.toml").decode())
# Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(constants["LOGGING"]["WIZARD_LOGGER"])

class Client:
    """Represents the main metadata wizard client."""

    def __init__(
        self,
        project_id: str,
        llm_location: str,
        dataplex_location: str,
        client_options: ClientOptions = None,
        llm_project_id: str | None = None,
    ):
        if client_options:
            self._client_options = client_options
        else:
            self._client_options = ClientOptions()

        self._utils = MetadataUtils(self)
        self._utils.validate_project_id(project_id)
        self._utils.validate_location(llm_location)
        self._utils.validate_location(dataplex_location)

        if llm_project_id is not None and not isinstance(llm_project_id, str):
            raise ValueError("Invalid llm_project_id.")
        resolved_llm_project = (
            (llm_project_id or "").strip()
            or os.environ.get("METADATA_WIZARD_LLM_PROJECT_ID", "").strip()
            or project_id
        )
        self._utils.validate_project_id(resolved_llm_project)

        self._project_id = project_id
        self._llm_project_id = resolved_llm_project
        self._dataplex_location = dataplex_location
        self.llm_location = llm_location
        self._llm_calls_count = 0

        # Initialize cloud clients
        self._cloud_clients = {
            constants["CLIENTS"]["BIGQUERY"]: bigquery.Client(),
            constants["CLIENTS"]["DATAPLEX_DATA_SCAN"]: dataplex_v1.DataScanServiceClient(),
            constants["CLIENTS"]["DATA_CATALOG_LINEAGE"]: datacatalog_lineage_v1.LineageClient(),
            constants["CLIENTS"]["DATAPLEX_CATALOG"]: dataplex_v1.CatalogServiceClient()
        }

        # Initialize operation classes
        self._table_ops = TableOperations(self)
        self._column_ops = ColumnOperations(self)
        self._dataplex_ops = DataplexOperations(self)
        self._bigquery_ops = BigQueryOperations(self)
        self._review_ops = ReviewOperations(self)

    def reset_request_counters(self) -> None:
        """Reset per-request resource usage counters."""
        self._llm_calls_count = 0

    # Delegate all operations to appropriate operation classes
    def generate_dataset_tables_descriptions(self, dataset_fqn, strategy="NAIVE", documentation_csv_uri=None):
        return self._table_ops.generate_dataset_tables_descriptions(dataset_fqn, strategy, documentation_csv_uri)

    def regenerate_dataset_tables_descriptions(self, dataset_fqn, strategy="NAIVE", documentation_csv_uri=None):
        return self._table_ops.regenerate_dataset_tables_descriptions(dataset_fqn, strategy, documentation_csv_uri)

    def generate_table_description(self, table_fqn, documentation_uri=None, human_comments=None):
        return self._table_ops.generate_table_description(table_fqn, documentation_uri, human_comments)

    def generate_columns_descriptions(self, table_fqn, documentation_uri=None, human_comments=None):
        return self._column_ops.generate_columns_descriptions(table_fqn, documentation_uri, human_comments)

    def regenerate_columns_descriptions(self, table_fqn, documentation_uri=None, human_comments=None):
        return self._column_ops.regenerate_columns_descriptions(table_fqn, documentation_uri, human_comments)

    def accept_table_draft_description(self, table_fqn):
        return self._dataplex_ops.accept_table_draft_description(table_fqn)

    def accept_column_draft_description(self, table_fqn, column_name):
        return self._dataplex_ops.accept_column_draft_description(table_fqn, column_name)

    def get_comments_to_table_draft_description(self, table_fqn):
        return self._review_ops.get_comments_to_table_draft_description(table_fqn)

    def get_negative_examples_to_table_draft_description(self, table_fqn):
        return self._review_ops.get_negative_examples_to_table_draft_description(table_fqn)

    def add_comment_to_table_draft_description(self, table_fqn, comment):
        return self._review_ops.add_comment_to_table_draft_description(table_fqn, comment)

    def add_comment_to_column_draft_description(self, table_fqn, column_name, comment):
        return self._review_ops.add_comment_to_column_draft_description(table_fqn, column_name, comment)

    def get_review_item_details(self, table_fqn: str, column_name: str = None):
        return self._review_ops.get_review_item_details(table_fqn, column_name)

    def mark_table_for_regeneration(self, table_fqn: str):
        return self._dataplex_ops.mark_table_for_regeneration(table_fqn)

    def mark_column_for_regeneration(self, table_fqn: str, column_name: str):
        return self._dataplex_ops.mark_column_for_regeneration(table_fqn, column_name)

    def generate_dataset_tables_columns_descriptions(self, dataset_fqn, strategy="NAIVE", documentation_csv_uri=None):
        return self._column_ops.generate_dataset_tables_columns_descriptions(dataset_fqn, strategy, documentation_csv_uri)

    def regenerate_dataset_tables_columns_descriptions(self, dataset_fqn, strategy="NAIVE", documentation_csv_uri=None):
        return self._column_ops.regenerate_dataset_tables_columns_descriptions(dataset_fqn, strategy, documentation_csv_uri)

    def reject_review_item(self, item_id: str):
        return self._review_ops.reject_review_item(item_id)
        
    def edit_review_item(self, item_id: str, description: str):
        return self._review_ops.edit_review_item(item_id, description) 