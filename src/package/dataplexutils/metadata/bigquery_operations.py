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
"""Dataplex Utils Metadata Wizard BigQuery operations
   2024 Google
"""
# Standard library imports
import logging
import toml
import pkgutil

# Cloud imports
from google.cloud.exceptions import NotFound
from google.cloud import bigquery
from google.api_core.exceptions import BadRequest, Forbidden

# Local imports
from .utils import sanitize_llm_output, validate_bq_job_id

# Load constants
constants = toml.loads(pkgutil.get_data(__name__, "constants.toml").decode())
# Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(constants["LOGGING"]["WIZARD_LOGGER"])

_MAX_SAMPLE_ROWS = 100


class BigQueryOperations:
    """BigQuery-specific operations."""

    def __init__(self, client):
        """Initialize with reference to main client."""
        self._client = client

    def _validate_and_format_fqn(self, table_fqn: str) -> str:
        project_id, dataset_id, table_id = self._client._utils.split_table_fqn(table_fqn)
        return f"{project_id}.{dataset_id}.{table_id}"

    def _check_overwrite_allowed(self, existing_description: str, target_label: str) -> None:
        handling = self._client._client_options._description_handling
        handling_lower = handling.lower() if isinstance(handling, str) else ""
        if (
            handling_lower == constants["DESCRIPTION_HANDLING"]["REPLACE"]
            and existing_description.strip()
            and not existing_description.lstrip().startswith(
                constants["OUTPUT_CLAUSES"]["AI_WARNING"]
            )
            and not self._client._client_options._confirm_overwrite
        ):
            raise ValueError(
                f"Refusing destructive replace of human-curated {target_label} description without confirm_overwrite=True."
            )

    def table_exists(self, table_fqn: str) -> None:
        """Checks if a specified BigQuery table exists."""
        safe_fqn = self._validate_and_format_fqn(table_fqn)
        try:
            self._client._cloud_clients[constants["CLIENTS"]["BIGQUERY"]].get_table(safe_fqn)
        except NotFound:
            logger.error(f"Table {safe_fqn} is not found.")
            raise NotFound(message=f"Table {safe_fqn} is not found.")

    def get_table_schema(self, table_fqn):
        """Retrieves the schema of a BigQuery table."""
        safe_fqn = self._validate_and_format_fqn(table_fqn)
        try:
            table = self._client._cloud_clients[constants["CLIENTS"]["BIGQUERY"]].get_table(
                safe_fqn
            )
            schema_fields = table.schema
            flattened_schema = [
                {"name": field.name, "type": field.field_type}
                for field in schema_fields
            ]
            return flattened_schema, table.schema
        except NotFound:
            logger.error(f"Table {safe_fqn} is not found.")
            raise NotFound(message=f"Table {safe_fqn} is not found.")

    def get_table_sample(self, table_fqn, num_rows_to_sample):
        """Retrieves a sample of rows from a BigQuery table."""
        safe_fqn = self._validate_and_format_fqn(table_fqn)
        if isinstance(num_rows_to_sample, bool) or not isinstance(num_rows_to_sample, int):
            raise ValueError("num_rows_to_sample must be an integer.")
        if num_rows_to_sample < 0 or num_rows_to_sample > _MAX_SAMPLE_ROWS:
            raise ValueError(
                f"num_rows_to_sample must be between 0 and {_MAX_SAMPLE_ROWS}."
            )
        if num_rows_to_sample == 0:
            return "[]"

        try:
            bq_client = self._client._cloud_clients[constants["CLIENTS"]["BIGQUERY"]]
            query = f"SELECT * FROM `{safe_fqn}` LIMIT {num_rows_to_sample}"
            return bq_client.query(query).to_dataframe().to_json()
        except (BadRequest, Forbidden) as e:
            logger.warning(f"BigQuery error when sampling table {safe_fqn}: {e}")
            return "[]"
        except Exception as e:
            logger.error(f"Exception: {e}.")
            raise e

    def get_table_description(self, table_fqn):
        """Retrieves the current description of a BigQuery table."""
        safe_fqn = self._validate_and_format_fqn(table_fqn)
        try:
            table = self._client._cloud_clients[constants["CLIENTS"]["BIGQUERY"]].get_table(
                safe_fqn
            )
            return table.description
        except Exception as e:
            logger.error(f"Exception: {e}.")
            raise e

    def update_table_description(self, table_fqn, description):
        """Updates the table description in BigQuery."""
        safe_fqn = self._validate_and_format_fqn(table_fqn)
        try:
            client = self._client._cloud_clients[constants["CLIENTS"]["BIGQUERY"]]
            table = client.get_table(safe_fqn)

            existing_description = table.description or ""
            self._check_overwrite_allowed(existing_description, "table")

            sanitized_new = sanitize_llm_output(
                description, max_chars=constants["DATA"]["MAX_TABLE_DESC_LENGTH"]
            )
            combined_description = self._client._utils.combine_description(
                existing_description,
                sanitized_new,
                self._client._client_options._description_handling,
                max_length=constants["DATA"]["MAX_TABLE_DESC_LENGTH"],
            )

            logger.info(
                "Audit: updating BigQuery table description for %s (prior_len=%d, prior_preview=%r)",
                safe_fqn,
                len(existing_description),
                existing_description[:200],
            )
            table.description = combined_description
            client.update_table(table, ["description"])

            logger.info(f"Updated description for table {safe_fqn}")
            return True
        except Exception as e:
            logger.error(f"Exception updating table description: {e}.")
            raise e

    def update_column_description(self, table_fqn, column_name, description):
        """Updates the column description in BigQuery."""
        safe_fqn = self._validate_and_format_fqn(table_fqn)
        self._client._utils.validate_column_name(column_name)
        try:
            client = self._client._cloud_clients[constants["CLIENTS"]["BIGQUERY"]]
            table = client.get_table(safe_fqn)

            schema = list(table.schema)
            found = False
            for i, field in enumerate(schema):
                if field.name == column_name:
                    found = True
                    existing_description = field.description or ""
                    self._check_overwrite_allowed(existing_description, "column")

                    sanitized_new = sanitize_llm_output(
                        description, max_chars=constants["DATA"]["MAX_COLUMN_DESC_LENGTH"]
                    )
                    combined_description = self._client._utils.combine_description(
                        existing_description,
                        sanitized_new,
                        self._client._client_options._description_handling,
                        max_length=constants["DATA"]["MAX_COLUMN_DESC_LENGTH"],
                    )

                    logger.info(
                        "Audit: updating BigQuery column description for %s.%s (prior_len=%d, prior_preview=%r)",
                        safe_fqn,
                        column_name,
                        len(existing_description),
                        existing_description[:200],
                    )
                    extra_kwargs = {
                        attr: getattr(field, attr)
                        for attr in (
                            "default_value_expression",
                            "precision",
                            "scale",
                            "max_length",
                            "range_element_type",
                        )
                        if getattr(field, attr, None) is not None
                    }
                    new_field = bigquery.SchemaField(
                        name=field.name,
                        field_type=field.field_type,
                        mode=field.mode,
                        description=combined_description,
                        fields=field.fields,
                        policy_tags=field.policy_tags,
                        **extra_kwargs,
                    )
                    schema[i] = new_field
                    break

            if not found:
                raise ValueError("Column not found in table schema.")

            table.schema = schema
            client.update_table(table, ["schema"])

            logger.info(f"Updated description for column {column_name} in table {safe_fqn}")
            return True
        except Exception as e:
            logger.error(f"Exception updating column description: {e}.")
            raise e

    def get_job_query(self, bq_job_id: str, job_location: str):
        """Retrieves the query text associated with a BigQuery job."""
        validate_bq_job_id(bq_job_id)
        self._client._utils.validate_location(job_location)
        try:
            bq_client = self._client._cloud_clients[constants["CLIENTS"]["BIGQUERY"]]
            job = bq_client.get_job(job_id=bq_job_id, location=job_location)
            if hasattr(job, 'query') and job.query:
                return job.query
            else:
                logger.warning(f"Job {bq_job_id} in location {job_location} does not have query information.")
                return None
        except NotFound:
            logger.warning(f"BigQuery job {bq_job_id} not found in location {job_location}.")
            return None
        except Exception as e:
            logger.error(f"Exception retrieving query for job {bq_job_id} in location {job_location}: {e}")
            raise e

    def update_table_schema(self, table_fqn, schema):
        """Updates the schema of a BigQuery table."""
        safe_fqn = self._validate_and_format_fqn(table_fqn)
        try:
            bq_client = self._client._cloud_clients[constants["CLIENTS"]["BIGQUERY"]]
            table = bq_client.get_table(safe_fqn)

            prior_by_name = {
                f.name: (f.description or "") for f in (table.schema or [])
            }
            for new_field in schema:
                old_desc = prior_by_name.get(new_field.name, "")
                new_desc = getattr(new_field, "description", None) or ""
                if new_desc != old_desc:
                    self._check_overwrite_allowed(old_desc, f"column '{new_field.name}'")
                    logger.info(
                        "Audit: updating schema description for %s.%s (prior_len=%d, prior_preview=%r)",
                        safe_fqn,
                        new_field.name,
                        len(old_desc),
                        old_desc[:200],
                    )

            table.schema = schema
            _ = bq_client.update_table(table, ["schema"])
        except Exception as e:
            logger.error(f"Exception: {e}.")
            raise e 