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

# Standard library imports
import logging
import toml
import pkgutil
import datetime
import uuid
import traceback
import json
import re

# Cloud imports
from google.cloud import dataplex_v1
from google.cloud.dataplex_v1 import (
    GetDataScanRequest,
    ListDataScanJobsRequest,
    GetDataScanJobRequest,
)
from google.cloud.dataplex_v1.types.datascans import DataScanJob
from google.protobuf import field_mask_pb2, struct_pb2, json_format
import google.api_core.exceptions
from google.cloud import datacatalog_lineage_v1

# Local imports
from .utils import sanitize_llm_output, validate_gcs_uri

# Load constants
constants = toml.loads(pkgutil.get_data(__name__, "constants.toml").decode())
# Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(constants["LOGGING"]["WIZARD_LOGGER"])

_MAX_DATA_SCANS = 5
_MAX_SCAN_JOBS_CHECKED = 10
_MAX_LINEAGE_SOURCES = 10
_MAX_LINEAGE_PROCESSES = 10

class DataplexOperations:
    """Dataplex-specific operations."""

    def __init__(self, client):
        """Initialize with reference to main client."""
        self._client = client

    def _check_if_exists_aspect_type(self, aspect_type_id: str):
        """Checks if a specified aspect type exists in Dataplex catalog.

        Args:
            aspect_type_id (str): The ID of the aspect type to check

        Returns:
            bool: True if the aspect type exists, False otherwise

        Raises:
            Exception: If there is an error checking the aspect type existence
                beyond a NotFound error
        """
        # Create a client
        client = self._client._cloud_clients[constants["CLIENTS"]["DATAPLEX_CATALOG"]]

        # Initialize request argument(s)
        request = dataplex_v1.GetAspectTypeRequest(
            name=f"projects/{self._client._project_id}/locations/global/aspectTypes/{aspect_type_id}"
        )
        
        # Make the request
        try:
            client.get_aspect_type(request=request)
            return True
        except google.api_core.exceptions.NotFound:
            return False

    def _create_aspect_type(self, aspect_type_id: str):
        """Creates a new aspect type in Dataplex catalog.

        Args:
            aspect_type_id (str): The ID to use for the new aspect type

        Raises:
            Exception: If there is an error creating the aspect type
        """
        # Create a client
        client = self._client._cloud_clients[constants["CLIENTS"]["DATAPLEX_CATALOG"]]

        # Initialize request argument(s)
        aspect_type = dataplex_v1.AspectType()
        full_metadata_template = {
            "type_": constants["ASPECT_TEMPLATE"]["type_"],
            "name": constants["ASPECT_TEMPLATE"]["name"],
            "record_fields": constants["record_fields"]
        }
        metadata_template = dataplex_v1.AspectType.MetadataTemplate(full_metadata_template)

        logger.info("Will deploy following template:" + str(metadata_template))
        
        aspect_type.metadata_template = metadata_template
        aspect_type.display_name = constants["ASPECT_TEMPLATE"]["display_name"]

        request = dataplex_v1.CreateAspectTypeRequest(
            parent=f"projects/{self._client._project_id}/locations/global",
            aspect_type_id=aspect_type_id,
            aspect_type=aspect_type,
        )

        # Make the request
        try:
            operation = client.create_aspect_type(request=request)
        except Exception as e:
            logger.error(f"Failed to create aspect type: {e}")
            raise e

    def update_table_dataplex_description(self, table_fqn, description):
        """Updates the table description in Dataplex.

        Args:
            table_fqn (str): The fully qualified name of the table
            description (str): The new description to set

        Returns:
            bool: True if successful

        Raises:
            Exception: If there is an error updating the description
        """
        try:
            project_id, dataset_id, table_id = self._client._utils.split_table_fqn(table_fqn)
            client = self._client._cloud_clients[constants["CLIENTS"]["DATAPLEX_CATALOG"]]

            entry_name = f"projects/{project_id}/locations/{self._get_dataset_location(table_fqn)}/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}"
            aspect_type = f"""projects/dataplex-types/locations/global/aspectTypes/overview"""
            aspect_types = [aspect_type]
            old_overview = None
            aspect_content = None

            try:
                request = dataplex_v1.GetEntryRequest(name=entry_name, view=dataplex_v1.EntryView.CUSTOM, aspect_types=aspect_types)
                current_entry = client.get_entry(request=request)
                for i in current_entry.aspects:
                    if i.endswith(f"""global.overview""") and current_entry.aspects[i].path == "":
                        logger.info(f"Reading existing aspect {i} of table {table_fqn}")
                        old_overview = dict(current_entry.aspects[i].data)
                        old_preview = (old_overview.get("content") or "")[:50]
                        logger.info(f"old_overview: {old_preview}...")
            except Exception as e:
                logger.error(f"Exception: {e}.")
                raise e

            # Create the aspect
            aspect = dataplex_v1.Aspect()
            aspect.aspect_type = aspect_type
            aspect_content = {}

            if old_overview is not None:
                old_description = old_overview.get("content", "") or ""
                self._client._bigquery_ops._check_overwrite_allowed(
                    old_description, "Dataplex overview"
                )
                sanitized_desc = sanitize_llm_output(
                    description, max_chars=constants["DATA"]["MAX_TABLE_DESC_LENGTH"]
                )
                combined_description = self._client._utils.combine_description(
                    old_description,
                    sanitized_desc,
                    self._client._client_options._description_handling,
                    max_length=constants["DATA"]["MAX_TABLE_DESC_LENGTH"],
                )
                logger.debug(f"""FINAL combined_description: {combined_description}""")
                aspect_content["content"] = combined_description
            else:
                aspect_content = {
                    "content": sanitize_llm_output(
                        description, max_chars=constants["DATA"]["MAX_TABLE_DESC_LENGTH"]
                    )
                }

            logger.info(f"""aspect_content: {aspect_content}...""")
            # Convert aspect_content to a Struct
            data_struct = struct_pb2.Struct()
            data_struct.update(aspect_content)
            aspect.data = data_struct

            overview_path = f"dataplex-types.global.overview"

            logger.info(f"project_id: {project_id}, dataset_id: {dataset_id}, table_id: {table_id}")
            entry = dataplex_v1.Entry()
            entry.name = entry_name
            entry.aspects[overview_path] = aspect

            # Initialize request argument(s)
            request = dataplex_v1.UpdateEntryRequest(
                entry=entry,
                update_mask=field_mask_pb2.FieldMask(paths=["aspects"]),
            )

            # Make the request
            try:
                response = client.update_entry(request=request)
                logger.info(f"Aspect created: {response.name}")
                return True
            except Exception as e:
                logger.error(f"Failed to create aspect: {e}")
                return False

        except Exception as e:
            logger.error(f"Exception: {e}.")
            raise e

    def update_table_draft_description(self, table_fqn, description, metadata=None):
        """Updates the draft description for a table in Dataplex.

        Args:
            table_fqn (str): The fully qualified name of the table
            description (str): The new draft description
            metadata (dict, optional): Additional metadata to include

        Returns:
            bool: True if successful

        Raises:
            Exception: If there is an error updating the draft description
        """
        try:
            client = self._client._cloud_clients[constants["CLIENTS"]["DATAPLEX_CATALOG"]]
            project_id, dataset_id, table_id = self._client._utils.split_table_fqn(table_fqn)
            safe_fqn = f"{project_id}.{dataset_id}.{table_id}"
            sanitized_desc = sanitize_llm_output(
                description, max_chars=constants["DATA"]["MAX_TABLE_DESC_LENGTH"]
            )
            # Create new aspect content
            new_aspect_content = {
                "contents": sanitized_desc,
                "generation-date": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to-be-regenerated": "false",
                "is-accepted": "false"
            }

            # If additional metadata was provided, validate and update the aspect content
            safe_metadata = None
            if metadata:
                safe_metadata = dict(metadata)
                if "external-document-uri" in safe_metadata:
                    ext_uri = safe_metadata["external-document-uri"]
                    if ext_uri is not None:
                        if not isinstance(ext_uri, str):
                            raise ValueError("Invalid external document URI.")
                        ext_uri = ext_uri.strip()
                        if ext_uri:
                            validate_gcs_uri(ext_uri)
                        safe_metadata["external-document-uri"] = ext_uri
                if "contents" in safe_metadata and safe_metadata["contents"] is not None:
                    safe_metadata["contents"] = sanitize_llm_output(
                        safe_metadata["contents"],
                        max_chars=constants["DATA"]["MAX_TABLE_DESC_LENGTH"],
                    )
                new_aspect_content.update(safe_metadata)

            logger.info(f"Updating draft aspect for table {safe_fqn}")
            
            # Create the aspect
            new_aspect = dataplex_v1.Aspect()
            new_aspect.aspect_type = f"""projects/{self._client._project_id}/locations/global/aspectTypes/{constants["ASPECT_TEMPLATE"]["name"]}"""
            aspect_name = f"""{self._client._project_id}.global.{constants["ASPECT_TEMPLATE"]["name"]}"""
            aspect_types = [new_aspect.aspect_type]

            entry = dataplex_v1.Entry()
            entry.name = f"projects/{project_id}/locations/{self._get_dataset_location(safe_fqn)}/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}"

            # Check if the aspect already exists
            try:
                get_request = dataplex_v1.GetEntryRequest(name=entry.name, view=dataplex_v1.EntryView.CUSTOM, aspect_types=aspect_types)
                entry = client.get_entry(request=get_request)
            except Exception as e:
                logger.error(f"Exception: {e}.")
                raise e

            data_struct = struct_pb2.Struct()
            data_struct.update(new_aspect_content)
            new_aspect.data = data_struct
            for i in entry.aspects:
                if i.endswith(f"""global.{constants["ASPECT_TEMPLATE"]["name"]}""") and entry.aspects[i].path == "":
                    logger.info(f"Updating aspect {i} with old_values")
                    new_aspect.data = entry.aspects[i].data
                    update_data = {
                        "contents": sanitized_desc,
                        "generation-date": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "to-be-regenerated": "false",
                        "is-accepted": "false"
                    }
                    if safe_metadata:
                        update_data.update(safe_metadata)
                    new_aspect.data.update(update_data)
                    logger.info(f"entry.aspects[aspect_name].data: {entry.aspects[i].data}")
                    logger.info(f"new_aspect.data: {new_aspect.data}")

            new_entry = dataplex_v1.Entry()
            new_entry.name = entry.name
            new_entry.aspects[aspect_name] = new_aspect

            # Initialize request argument(s)  
            request = dataplex_v1.UpdateEntryRequest(
                entry=new_entry,
                update_mask=field_mask_pb2.FieldMask(paths=["aspects"]), 
                allow_missing=False,
                aspect_keys=[aspect_name]
            )
            # Make the request
            try:
                response = client.update_entry(request=request)
                logger.info(f"Aspect created: {response.name}")
                return True
            except Exception as e:
                logger.error(f"Failed to create aspect: {e}")
                return False

            return True

        except Exception as e:
            logger.error(f"Exception: {e}.")
            raise e

    def accept_table_draft_description(self, table_fqn):
        """Accepts the draft description for a table, promoting it to the actual table description.
        
        Args:
            table_fqn (str): The fully qualified name of the table
            
        Returns:
            bool: True if successful
            
        Raises:
            Exception: If there is an error accepting the draft description
        """
        try:
            # Create a client
            client = self._client._cloud_clients[constants["CLIENTS"]["DATAPLEX_CATALOG"]]
            
            # Get project and dataset IDs
            project_id, dataset_id, table_id = self._client._utils.split_table_fqn(table_fqn)
            
            # Set up aspect types and entry name
            aspect_types = [
                f"""projects/{self._client._project_id}/locations/global/aspectTypes/{constants["ASPECT_TEMPLATE"]["name"]}"""
            ]
            
            entry_name = f"projects/{project_id}/locations/{self._get_dataset_location(table_fqn)}/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}"
            
            # Get the entry with the draft aspect
            request = dataplex_v1.GetEntryRequest(
                name=entry_name,
                view=dataplex_v1.EntryView.CUSTOM,
                aspect_types=aspect_types
            )
            
            entry = client.get_entry(request=request)
            overview = None
            
            # Find the draft description in the custom aspect
            for aspect_key, aspect in entry.aspects.items():
                logger.info(f"Processing aspect: {aspect_key}")
                if aspect.aspect_type.endswith(f"""aspectTypes/{constants["ASPECT_TEMPLATE"]["name"]}""") and aspect.path == "":
                    if "contents" in aspect.data:
                        overview = aspect.data["contents"]
                        logger.info(f"Found draft description: {overview[:50]}...")
                        break
            

            if overview:
                # Update BigQuery first so any overwrite guard failure avoids partial Dataplex mutation
                logger.info(f"Updating BigQuery description: {overview}")
                success_bigquery = self._client._bigquery_ops.update_table_description(table_fqn, overview)
                if not success_bigquery:
                    logger.warning(f"BigQuery update returned False for table {table_fqn}; skipping Dataplex overview update")
                    return False
                logger.info(f"Updating Dataplex description: {overview}")
                success_dataplex = self.update_table_dataplex_description(table_fqn, overview)

                if success_dataplex and success_bigquery:
                    logger.info(f"Successfully updated description for table {table_fqn}")
                    return True
                else:
                    logger.warning(f"Partial update for table {table_fqn}: Dataplex={success_dataplex}, BigQuery={success_bigquery}")
                    return False
            else:
                logger.warning(f"No draft description found for table {table_fqn}")
                return False
                
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Exception in accept_table_draft_description: {e}")
            return False

    def update_column_draft_description(self, table_fqn, column_name, description):
        """Updates the draft description for a column in Dataplex."""
        try:
            project_id, dataset_id, table_id = self._client._utils.split_table_fqn(table_fqn)
            self._client._utils.validate_column_name(column_name)
            safe_fqn = f"{project_id}.{dataset_id}.{table_id}"
            sanitized_desc = sanitize_llm_output(
                description, max_chars=constants["DATA"]["MAX_COLUMN_DESC_LENGTH"]
            )
            client = self._client._cloud_clients[constants["CLIENTS"]["DATAPLEX_CATALOG"]]

            # Create new aspect content
            new_aspect_content = {
                "contents": sanitized_desc,
                "generation-date": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to-be-regenerated": "false",
                "is-accepted": "false"
            }

            logger.info(f"Updating draft aspect for column {safe_fqn}.{column_name}")

            # Create the aspect
            new_aspect = dataplex_v1.Aspect()
            new_aspect.aspect_type = f"""projects/{self._client._project_id}/locations/global/aspectTypes/{constants["ASPECT_TEMPLATE"]["name"]}"""
            aspect_name = f"""{self._client._project_id}.global.{constants["ASPECT_TEMPLATE"]["name"]}@Schema.{column_name}"""
            aspect_types = [new_aspect.aspect_type]

            entry = dataplex_v1.Entry()
            entry.name = f"projects/{project_id}/locations/{self._get_dataset_location(safe_fqn)}/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}"

            # Check if the aspect already exists
            try:
                get_request = dataplex_v1.GetEntryRequest(name=entry.name, view=dataplex_v1.EntryView.CUSTOM, aspect_types=aspect_types)
                entry = client.get_entry(request=get_request)
            except Exception as e:
                logger.error(f"Exception: {e}.")
                raise e

            data_struct = struct_pb2.Struct()
            data_struct.update(new_aspect_content)
            new_aspect.data = data_struct

            for i in entry.aspects:
                logger.info(f"""i: {i} path: "{entry.aspects[i].path}" """)
                if i.endswith(f"""global.{constants["ASPECT_TEMPLATE"]["name"]}@Schema.{column_name}""") and entry.aspects[i].path == f"Schema.{column_name}":
                    logger.info(f"Updating aspect {i} with new values")
                    new_aspect.data = entry.aspects[i].data
                    new_aspect.data.update({
                        "contents": sanitized_desc,
                        "generation-date": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "to-be-regenerated": "false",
                        "is-accepted": "false"
                    })

            new_entry = dataplex_v1.Entry()
            new_entry.name = entry.name
            new_entry.aspects[aspect_name] = new_aspect

            # Initialize request argument(s)  
            request = dataplex_v1.UpdateEntryRequest(
                entry=new_entry,
                update_mask=field_mask_pb2.FieldMask(paths=["aspects"]), 
                allow_missing=False,
                aspect_keys=[aspect_name]
            )

            # Make the request
            try:
                response = client.update_entry(request=request)
                logger.info(f"Aspect created: {response.name}")
                return True
            except Exception as e:
                logger.error(f"Failed to create aspect: {e}")
                return False

        except Exception as e:
            logger.error(f"Exception: {e}.")
            raise e

    def check_if_table_should_be_regenerated(self, table_fqn):
        """Checks if a table should be regenerated.

        Args:
            table_fqn (str): The fully qualified name of the table

        Returns:
            bool: True if the table should be regenerated
        """
        try:
            client = self._client._cloud_clients[constants["CLIENTS"]["DATAPLEX_CATALOG"]]
            project_id, dataset_id, table_id = self._client._utils.split_table_fqn(table_fqn)

            entry = dataplex_v1.Entry()
            entry.name = f"projects/{project_id}/locations/{self._get_dataset_location(table_fqn)}/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}"
            aspect_types = [f"""projects/{self._client._project_id}/locations/global/aspectTypes/{constants["ASPECT_TEMPLATE"]["name"]}"""]

            try:
                get_request = dataplex_v1.GetEntryRequest(name=entry.name, view=dataplex_v1.EntryView.CUSTOM, aspect_types=aspect_types)
                entry = client.get_entry(request=get_request)
            except Exception as e:
                logger.error(f"Exception: {e}.")
                raise e

            for i in entry.aspects:
                if i.endswith(f"""global.{constants["ASPECT_TEMPLATE"]["name"]}""") and entry.aspects[i].path == "":
                    data_dict = entry.aspects[i].data
                    return data_dict["to-be-regenerated"] == True

            return False

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Exception: {e}.")
            return False

    def check_if_column_should_be_regenerated(self, table_fqn, column_name):
        """Checks if a column should be regenerated."""
        self._client._utils.validate_column_name(column_name)
        try:
            client = self._client._cloud_clients[constants["CLIENTS"]["DATAPLEX_CATALOG"]]
            project_id, dataset_id, table_id = self._client._utils.split_table_fqn(table_fqn)

            entry = dataplex_v1.Entry()
            entry.name = f"projects/{project_id}/locations/{self._get_dataset_location(table_fqn)}/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}"
            aspect_types = [f"""projects/{self._client._project_id}/locations/global/aspectTypes/{constants["ASPECT_TEMPLATE"]["name"]}"""]

            try:
                get_request = dataplex_v1.GetEntryRequest(name=entry.name, view=dataplex_v1.EntryView.CUSTOM, aspect_types=aspect_types)
                entry = client.get_entry(request=get_request)
            except Exception as e:
                logger.error(f"Exception: {e}.")
                raise e

            for i in entry.aspects:
                if i.endswith(f"""global.{constants["ASPECT_TEMPLATE"]["name"]}@Schema.{column_name}""") and entry.aspects[i].path == f"Schema.{column_name}":
                    data_dict = entry.aspects[i].data
                    return data_dict["to-be-regenerated"] == True

            return False

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Exception: {e}.")
            return False

    def get_column_comment(self, table_fqn, column_name, comment_number=None):
        """Gets comments for a column."""
        self._client._utils.validate_column_name(column_name)
        try:
            client = self._client._cloud_clients[constants["CLIENTS"]["DATAPLEX_CATALOG"]]
            project_id, dataset_id, table_id = self._client._utils.split_table_fqn(table_fqn)

            entry_name = f"projects/{project_id}/locations/{self._get_dataset_location(table_fqn)}/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}"
            aspect_type = f"""projects/{self._client._project_id}/locations/global/aspectTypes/{constants["ASPECT_TEMPLATE"]["name"]}"""
            aspect_types = [aspect_type]

            request = dataplex_v1.GetEntryRequest(name=entry_name, view=dataplex_v1.EntryView.CUSTOM, aspect_types=aspect_types)
            entry = client.get_entry(request=request)

            comments = []
            for aspect in entry.aspects:
                if aspect.endswith(f"""global.{constants["ASPECT_TEMPLATE"]["name"]}@Schema.{column_name}""") and entry.aspects[aspect].path == f"Schema.{column_name}":
                    if "human-comments" in entry.aspects[aspect].data:
                        if comment_number is None:
                            comments.extend(entry.aspects[aspect].data["human-comments"])
                        else:
                            comments.append(entry.aspects[aspect].data["human-comments"][comment_number])

            return comments

        except Exception as e:
            logger.error(f"Exception: {e}.")
            raise e

    def get_table_comment(self, table_fqn, comment_number=None):
        """Gets comments for a table.

        Args:
            table_fqn (str): The fully qualified name of the table
            comment_number (int, optional): Specific comment number to retrieve

        Returns:
            list: List of comments or specific comment if comment_number provided
        """
        try:
            client = self._client._cloud_clients[constants["CLIENTS"]["DATAPLEX_CATALOG"]]
            project_id, dataset_id, table_id = self._client._utils.split_table_fqn(table_fqn)

            entry_name = f"projects/{project_id}/locations/{self._get_dataset_location(table_fqn)}/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}"
            aspect_type = f"""projects/{self._client._project_id}/locations/global/aspectTypes/{constants["ASPECT_TEMPLATE"]["name"]}"""
            aspect_types = [aspect_type]

            request = dataplex_v1.GetEntryRequest(name=entry_name, view=dataplex_v1.EntryView.CUSTOM, aspect_types=aspect_types)
            entry = client.get_entry(request=request)

            comments = []
            for aspect_key, aspect in entry.aspects.items():
                if aspect_key.endswith(f"""global.{constants["ASPECT_TEMPLATE"]["name"]}""") and aspect.path == "":
                    if "human-comments" in aspect.data:
                        if comment_number is None:
                            comments.extend(aspect.data["human-comments"])
                        else:
                            comments.append(aspect.data["human-comments"][comment_number])

            return comments

        except Exception as e:
            logger.error(f"Exception: {e}.")
            raise e

    def _get_dataset_location(self, table_fqn):
        """Gets the location of a dataset.

        Args:
            table_fqn (str): The fully qualified name of the table

        Returns:
            str: The dataset location
        """
        try:
            project_id, dataset_id, _ = self._client._utils.split_table_fqn(table_fqn)
            return str(self._client._cloud_clients[constants["CLIENTS"]["BIGQUERY"]].get_dataset(
                f"{project_id}.{dataset_id}"
            ).location).lower()
        except Exception as e:
            logger.error(f"Exception: {e}.")
            raise e

    def accept_column_draft_description(self, table_fqn, column_name):
        """Accepts the draft description for a column."""
        project_id, dataset_id, table_id = self._client._utils.split_table_fqn(table_fqn)
        self._client._utils.validate_column_name(column_name)
        safe_fqn = f"{project_id}.{dataset_id}.{table_id}"
        try:
            logger.info(f"=== START: accept_column_draft_description for {safe_fqn}.{column_name} ===")
            client = self._client._cloud_clients[constants["CLIENTS"]["DATAPLEX_CATALOG"]]

            aspect_type_id = constants["ASPECT_TEMPLATE"]["name"]
            aspect_type = f"projects/{self._client._project_id}/locations/global/aspectTypes/{aspect_type_id}"
            short_aspect_name_key = f"{aspect_type_id}@Schema.{column_name}"
            entry_name = f"projects/{project_id}/locations/{self._get_dataset_location(safe_fqn)}/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}"

            # 1. Get the current entry with the specific aspect type
            try:
                get_request = dataplex_v1.GetEntryRequest(
                    name=entry_name,
                    view=dataplex_v1.EntryView.CUSTOM,
                    aspect_types=[aspect_type] # Fetch aspects of this type
                )
                entry = client.get_entry(request=get_request)
                logger.info(f"Successfully retrieved entry: {entry_name}")
            except google.api_core.exceptions.NotFound:
                 logger.error(f"Entry not found: {entry_name}")
                 return False
            except Exception as e:
                 logger.error(f"Error getting entry {entry_name}: {e}")
                 raise e # Re-raise other exceptions

            draft_description = None
            aspect_found = False
            actual_aspect_key = None
            current_aspect_data = None

            new_aspect = dataplex_v1.Aspect()
            new_aspect.aspect_type = f"""projects/{self._client._project_id}/locations/global/aspectTypes/{constants["ASPECT_TEMPLATE"]["name"]}"""
            aspect_name = f"""{self._client._project_id}.global.{constants["ASPECT_TEMPLATE"]["name"]}@Schema.{column_name}"""
            aspect_types=[new_aspect.aspect_type]


            # 2. Find the specific column aspect and its data
            for key, aspect in entry.aspects.items():
                # Match based on the ending of the key AND the specific path
                if key.endswith(short_aspect_name_key) and aspect.path == f"Schema.{column_name}":
                    logger.info(f"Found matching aspect key: {key} with path {aspect.path}")
                    actual_aspect_key = key
                    new_aspect.data = aspect.data
                    aspect_found = True
                    new_aspect.data.update({
                        "is-accepted": True,
                        "when-accepted": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "to-be-regenerated": False
                    })
                    draft_description = aspect.data["contents"]
                    break # Found the correct aspect, no need to continue loop


            if not aspect_found or draft_description is None:
                logger.warning(f"No valid draft description content found for column {column_name}. Cannot accept.")
                return False

            new_entry = dataplex_v1.Entry()
            new_entry.name = entry.name
            new_entry.aspects[aspect_name] = new_aspect


            
            # --- Modify Update Mask and allow_missing --- START
            # update_mask = field_mask_pb2.FieldMask(paths=[f"aspects.\"{actual_aspect_key}\".data"]) # Update only the data part of this specific aspect
            
            # --- Modify Update Mask and allow_missing --- END
            
            request = dataplex_v1.UpdateEntryRequest(
                entry=new_entry,
                update_mask = field_mask_pb2.FieldMask(paths=["aspects"]), # Try updating the whole aspects field,
                allow_missing=False, # We confirmed the aspect exists
                aspect_keys=[aspect_name] # Specify exactly which aspect key to update
            )
            # -- Update BigQuery description 
            success_bq = self._client._bigquery_ops.update_column_description(table_fqn, column_name, draft_description)
           
            if success_bq:
                try:
                    # -- Update Dataplex aspect
                    logger.info(f"Attempting to update aspect {actual_aspect_key} for entry {entry_name}...")
                    client.update_entry(request=request)
                    logger.info(f"Successfully updated aspect metadata for column {column_name} to mark as accepted.")
                except Exception as e:
                    logger.error(f"Failed to update aspect metadata for column {column_name}: {e}")
                    logger.error(f"Traceback: {traceback.format_exc()}")
                    return False 

            
  
            
            logger.info(f"Successfully accepted and updated description for column {column_name} in table {safe_fqn}")
            return True

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Unexpected exception in accept_column_draft_description: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False
        finally:
             logger.info(f"=== END: accept_column_draft_description for {safe_fqn}.{column_name} ===")

    def _construct_bq_resource_string(self, table_fqn):
        """Constructs a BigQuery resource string for use in API calls.

        Args:
            table_fqn (str): The fully qualified name of the table
                (e.g., 'project.dataset.table')

        Returns:
            str: The constructed resource string in the format
                '//bigquery.googleapis.com/projects/{project}/datasets/{dataset}/tables/{table}'

        Raises:
            Exception: If there is an error constructing the resource string
        """
        try:
            # Assumes _split_table_fqn is available via self._client._utils
            project_id, dataset_id, table_id = self._client._utils.split_table_fqn(table_fqn)
            return f"//bigquery.googleapis.com/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}"
        except Exception as e:
            logger.error(f"Exception constructing BQ resource string: {e}.")
            raise e

    def _get_table_scan_reference(self, table_fqn):
        """Retrieves data scan references for a BigQuery table.

        Args:
            table_fqn (str): The fully qualified name of the table
                (e.g., 'project.dataset.table')

        Returns:
            list: List of scan reference names associated with the table

        Raises:
            Exception: If there is an error retrieving scan references
        """
        try:
            # Assumes DATAPLEX_DATA_SCAN client is available
            scan_client = self._client._cloud_clients.get(constants["CLIENTS"]["DATAPLEX_DATA_SCAN"])
            if not scan_client:
                 logger.error("Dataplex Data Scan client not initialized.")
                 return []
                 
            logger.info(f"Getting table scan reference for table:{table_fqn}.")

            # Get the dataset location
            dataset_location = self._get_dataset_location(table_fqn)
            if not dataset_location:
                logger.warning(f"Could not determine dataset location for table {table_fqn}, using default location from constants if available.")
                # Fallback or raise error depending on desired behavior
                dataset_location = constants.get("DEFAULT_LOCATION", "global") # Example fallback
            dataplex_location = self._client._dataplex_location
            parent_resource=f"projects/{self._client._project_id}/locations/{dataplex_location}"
            logger.info(f"Listing data scans in parent: {parent_resource}")
            
            # Ensure the parent exists or handle potential errors during list_data_scans
            try:
                 data_scans = scan_client.list_data_scans(
                     parent=parent_resource
                 )
            except google.api_core.exceptions.NotFound:
                 logger.warning(f"Parent resource for data scans not found: {parent_resource}")
                 return []
            except Exception as e:
                 logger.error(f"Error listing data scans in {parent_resource}: {e}")
                 raise e # Re-raise other exceptions

            bq_resource_string = self._construct_bq_resource_string(table_fqn)
            logger.info(f"Looking for scans matching resource: {bq_resource_string}")
            scan_references = []
            count = 0
            for scan in data_scans:
                count += 1
                if hasattr(scan, 'data') and hasattr(scan.data, 'resource') and scan.data.resource == bq_resource_string:
                    logger.info(f"Found matching scan: {scan.name}")
                    scan_references.append(scan.name)
                    if len(scan_references) >= _MAX_DATA_SCANS:
                        break
            logger.info(f"Checked {count} scans. Found {len(scan_references)} matching references.")
            return scan_references
        except Exception as e:
            logger.error(f"Exception getting table scan reference: {e}.")
            # Return empty list instead of raising exception to allow the process to continue
            return []

    def get_table_profile(self, use_enabled, table_fqn):
        """Retrieves the profile information for a BigQuery table.

        Args:
            use_enabled (bool): Whether profile retrieval is enabled
            table_fqn (str): The fully qualified name of the table
                (e.g., 'project.dataset.table')

        Returns:
            list: Table profile results, or empty list if disabled/not available

        Raises:
            Exception: If there is an error retrieving the table profile
        """
        try:
            table_profile = self.get_table_profile_quality(use_enabled, table_fqn)["data_profile"]
            if not table_profile:
                logger.info(f"No profile found for table in datascans{table_fqn}.")
                #self._client_options._use_profile = False
            return table_profile
        except Exception as e:
            logger.error(f"Exception: {e}.")
            raise e

    def get_table_quality(self, use_enabled, table_fqn):
        """Retrieves the data quality information for a BigQuery table.

        Args:
            use_enabled (bool): Whether quality check retrieval is enabled
            table_fqn (str): The fully qualified name of the table
                (e.g., 'project.dataset.table')

        Returns:
            list: Data quality results, or empty list if disabled/not available

        Raises:
            Exception: If there is an error retrieving quality information
        """
        try:
            table_quality = self.get_table_profile_quality(use_enabled, table_fqn)["data_quality"]
            # If the user is requesting to use data quality but there is
            # not data quality information to return, we disable the client
            # options flag so the prompt do not include this.
            if not table_quality:
                logger.info(f"No quality check found for table in datascans{table_fqn}.")
                #self._client_options._use_data_quality = False
            return table_quality
        except Exception as e:
            logger.error(f"Exception: {e}.")
            raise e

    def get_table_profile_quality(self, use_enabled, table_fqn):
        """Retrieves both profile and quality information for a BigQuery table.

        Args:
            use_enabled (bool): Whether profile/quality retrieval is enabled
            table_fqn (str): The fully qualified name of the table
                (e.g., 'project.dataset.table')

        Returns:
            dict: Dictionary containing:
                - data_profile (list): Profile results
                - data_quality (list): Quality results
                Both will be empty lists if disabled/not available

        Raises:
            Exception: If there is an error retrieving the information
        """
        try:
            if use_enabled:
                scan_client = self._client._cloud_clients[
                    constants["CLIENTS"]["DATAPLEX_DATA_SCAN"]
                ]
                data_profile_results = []
                data_quality_results = []
                table_scan_references = self._get_table_scan_reference(table_fqn)
                for table_scan_reference in table_scan_references[:_MAX_DATA_SCANS]:
                    if table_scan_reference:
                        jobs_checked = 0
                        for job in scan_client.list_data_scan_jobs(
                            ListDataScanJobsRequest(
                                parent=scan_client.get_data_scan(
                                    GetDataScanRequest(name=table_scan_reference)
                                ).name
                            )
                        ):
                            jobs_checked += 1
                            if jobs_checked > _MAX_SCAN_JOBS_CHECKED:
                                break
                            job_state = getattr(job, "state", None)
                            if job_state is not None and job_state != DataScanJob.State.SUCCEEDED:
                                continue
                            job_result = scan_client.get_data_scan_job(
                                request=GetDataScanJobRequest(
                                    name=job.name, view="FULL"
                                )
                            )
                            if job_result.state == DataScanJob.State.SUCCEEDED:
                                job_result_json = json.loads(
                                    dataplex_v1.types.datascans.DataScanJob.to_json(
                                        job_result
                                    )
                                )
                                if "dataQualityResult" in job_result_json:
                                    data_quality_results.append(
                                        job_result_json["dataQualityResult"]
                                    )
                                if "dataProfileResult" in job_result_json:
                                    data_profile_results.append(
                                        job_result_json["dataProfileResult"]
                                    )
                                break
                return {
                    "data_profile": data_profile_results,
                    "data_quality": data_quality_results,
                }
            else:
                return {
                    "data_profile": [],
                    "data_quality": [],
                }
        except Exception as e:
            logger.error(f"Exception: {e}.")
            raise e

    def get_table_sources_info(self, use_lineage_tables, table_fqn):
        """Gets source table information using Data Catalog Lineage API.

        Args:
            use_lineage_tables (bool): Whether to use lineage table information
            table_fqn (str): The fully qualified name of the table

        Returns:
            list: List of dictionaries containing source table info (name, schema, description)
                  or an empty list if not enabled or no sources found.
        """
        if not use_lineage_tables:
            return []

        try:
            table_sources_info = []
            # Assumes a _get_table_sources method exists or is added, similar to the backup
            table_sources = self._get_table_sources(table_fqn)
            for table_source in table_sources[:_MAX_LINEAGE_SOURCES]:
                # Assumes these helper methods exist on the BigQueryOperations part of the client
                source_schema, _ = self._client._bigquery_ops.get_table_schema(table_source) 
                source_description = self._client._bigquery_ops.get_table_description(table_source)
                table_sources_info.append(
                    {
                        "source_table_name": table_source,
                        "source_table_schema": source_schema,
                        "source_table_description": source_description,
                    }
                )
            # Check if the option should be disabled if no info was found
            if not table_sources_info:
                logger.warning(f"No lineage source table info found for {table_fqn}, disabling option for subsequent prompts if applicable.")
                # Consider if self._client._client_options._use_lineage_tables = False is needed here
            return table_sources_info
        except Exception as e:
            logger.error(f"Error getting table sources info for {table_fqn}: {e}")
            # Return empty list on error to avoid breaking flows
            return []

    def _get_table_sources(self, table_fqn):
        """Gets source tables for a given table using Data Catalog Lineage API.

        Args:
            table_fqn (str): The fully qualified name of the target table.

        Returns:
            list: A list of fully qualified names of source tables.
        """
        try:
            lineage_client = self._client._cloud_clients[constants["CLIENTS"]["DATA_CATALOG_LINEAGE"]]
            target = datacatalog_lineage_v1.EntityReference()
            target.fully_qualified_name = f"bigquery:{table_fqn}"
            target_dataset_location = str(self._get_dataset_location(table_fqn)).lower()
            logger.info(f"Searching for lineage links targeting table {table_fqn} in location {target_dataset_location}")

            request = datacatalog_lineage_v1.SearchLinksRequest(
                parent=f"projects/{self._client._project_id}/locations/{target_dataset_location}",
                target=target,
            )

            link_results = lineage_client.search_links(request=request)
            table_sources = []
            for link in link_results:
                # Ensure the link is pointing *to* the target table
                if link.target == target:
                    source_fqn = link.source.fully_qualified_name
                    if source_fqn.startswith("bigquery:"):
                         table_sources.append(source_fqn.replace("bigquery:", ""))
                    else:
                        logger.debug(f"Skipping non-BigQuery source: {source_fqn}")

            logger.info(f"Found {len(table_sources)} BigQuery source(s) for {table_fqn}")
            return table_sources
        except google.api_core.exceptions.NotFound:
            logger.warning(f"No lineage links found for table {table_fqn} in location {target_dataset_location}")
            return []
        except Exception as e:
            logger.error(f"Exception getting table sources for {table_fqn}: {e}")
            # Re-raise the exception after logging, or return [] depending on desired behavior
            raise e # Or return []

    def get_job_sources(self, use_lineage_processes, table_fqn):
        """Gets job source information using Data Catalog Lineage API.

        Args:
            use_lineage_processes (bool): Whether to use lineage process information
            table_fqn (str): The fully qualified name of the table

        Returns:
            list: List of BigQuery job SQL queries that produced the table,
                  or an empty list if not enabled or no jobs found.
        """
        if not use_lineage_processes:
            return []

        try:
            bq_process_sql = []
            lineage_client = self._client._cloud_clients[constants["CLIENTS"]["DATA_CATALOG_LINEAGE"]]
            target = datacatalog_lineage_v1.EntityReference()
            target.fully_qualified_name = f"bigquery:{table_fqn}"
            dataset_location = self._get_dataset_location(table_fqn)

            logger.info(f"Searching for lineage links targeting table {table_fqn} in location {dataset_location}")
            request = datacatalog_lineage_v1.SearchLinksRequest(
                parent=f"projects/{self._client._project_id}/locations/{dataset_location}",
                target=target,
            )

            try:
                link_results = lineage_client.search_links(request=request)
            except google.api_core.exceptions.NotFound:
                 logger.warning(f"No lineage links found for table {table_fqn} in location {dataset_location}")
                 return []
            except Exception as e:
                logger.error(f"Cannot find lineage links for table {table_fqn}: {e}")
                return [] # Return empty list on error

            # Ensure link_results.links exists and is iterable
            links = [link.name for link in link_results] # Access links via the response object directly

            if links:
                logger.info(f"Found {len(links)} lineage links for {table_fqn}. Searching for processes.")
                # Batch search for processes linked to the found links
                process_links = lineage_client.batch_search_link_processes(
                    request=datacatalog_lineage_v1.BatchSearchLinkProcessesRequest(
                        parent=f"projects/{self._client._project_id}/locations/{dataset_location}",
                        links=links,
                    )
                )
                
                lineage_processes_ids = [process.process for process in process_links][:_MAX_LINEAGE_PROCESSES]
                logger.info(f"Found {len(lineage_processes_ids)} associated processes.")

                for process_id in lineage_processes_ids:
                    try:
                        process_details = lineage_client.get_process(
                            request=datacatalog_lineage_v1.GetProcessRequest(
                                name=process_id,
                            )
                        )
                        # Check if 'attributes' exists and contains 'bigquery_job_id'
                        if hasattr(process_details, 'attributes') and "bigquery_job_id" in process_details.attributes:
                            bq_job_id = process_details.attributes["bigquery_job_id"]
                            logger.info(f"Found BigQuery Job ID {bq_job_id} for process {process_id}")
                            # Assumes a helper method exists on BigQueryOperations
                            job_query = self._client._bigquery_ops.get_job_query(bq_job_id, dataset_location)
                            if job_query:
                                bq_process_sql.append(job_query)
                        else:
                            logger.debug(f"Process {process_id} does not have a 'bigquery_job_id' attribute.")
                    except Exception as e:
                         logger.error(f"Error getting details for process {process_id}: {e}")

                # Check if the option should be disabled if no info was found
                if not bq_process_sql:
                    logger.warning(f"No BigQuery job source info found for {table_fqn}, disabling option for subsequent prompts if applicable.")
                    # Consider if self._client._client_options._use_lineage_processes = False is needed here
                return bq_process_sql
            else:
                logger.info(f"No lineage links found connecting to table {table_fqn}.")
                # Consider if self._client._client_options._use_lineage_processes = False is needed here
                return []
        except Exception as e:
            logger.error(f"Exception getting job sources for {table_fqn}: {e}")
            return [] # Return empty list on error

    def mark_table_for_regeneration(self, table_fqn: str) -> bool:
        """Marks a table for regeneration by setting the to-be-regenerated flag in its metadata.

        Args:
            table_fqn (str): The fully qualified name of the table (e.g., 'project.dataset.table')

        Returns:
            bool: True if the table was successfully marked for regeneration, False otherwise.

        Raises:
            Exception: If there is an error updating the table metadata in Dataplex
        """
        try:
            client = self._client._cloud_clients[constants["CLIENTS"]["DATAPLEX_CATALOG"]]
            project_id, dataset_id, table_id = self._client._utils.split_table_fqn(table_fqn)

            # Create entry name
            entry_name = f"projects/{project_id}/locations/{self._get_dataset_location(table_fqn)}/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}"
            aspect_type = f"""projects/{self._client._project_id}/locations/global/aspectTypes/{constants["ASPECT_TEMPLATE"]["name"]}"""
            aspect_name = f"""{self._client._project_id}.global.{constants["ASPECT_TEMPLATE"]["name"]}"""
            aspect_types = [aspect_type]

            # Get existing entry with aspects
            request = dataplex_v1.GetEntryRequest(
                name=entry_name,
                view=dataplex_v1.EntryView.CUSTOM,
                aspect_types=aspect_types
            )
            entry = client.get_entry(request=request)

            # Create new aspect
            new_aspect = dataplex_v1.Aspect()
            new_aspect.aspect_type = aspect_type

            # Update or create aspect data
            for i in entry.aspects:
                if i.endswith(f"""global.{constants["ASPECT_TEMPLATE"]["name"]}""") and entry.aspects[i].path=="":
                    logger.info(f"Updating existing aspect {i}")
                    new_aspect.data = entry.aspects[i].data
                    new_aspect.data.update({
                        "generation-date": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "to-be-regenerated": True
                    })
                    break
            else:
                # No existing aspect found, create new one
                aspect_data = {
                    "certified": "false",
                    "user-who-certified": "",
                    "contents": "",
                    "generation-date": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "to-be-regenerated": True,
                    "human-comments": [],
                    "negative-examples": [],
                    "external-document-uri": ""
                }
                data_struct = struct_pb2.Struct()
                data_struct.update(aspect_data)
                new_aspect.data = data_struct

            # Create new entry with updated aspect
            new_entry = dataplex_v1.Entry()
            new_entry.name = entry.name
            new_entry.aspects[aspect_name] = new_aspect

            # Update entry
            request = dataplex_v1.UpdateEntryRequest(
                entry=new_entry,
                update_mask=field_mask_pb2.FieldMask(paths=["aspects"]),
                allow_missing=False,
                aspect_keys=[aspect_name]
            )

            # Make the request
            response = client.update_entry(request=request)
            logger.info(f"Successfully marked table {table_fqn} for regeneration")
            return True

        except Exception as e:
            logger.error(f"Failed to mark table {table_fqn} for regeneration: {str(e)}")
            return False

    def mark_table_as_regenerated(self, table_fqn: str) -> bool:
        """Marks a table as regenerated by setting the to-be-regenerated flag to false in its metadata.

        Args:
            table_fqn (str): The fully qualified name of the table (e.g., 'project.dataset.table')

        Returns:
            bool: True if the table was successfully marked as regenerated, False otherwise.

        Raises:
            Exception: If there is an error updating the table metadata in Dataplex
        """
        try:
            client = self._client._cloud_clients[constants["CLIENTS"]["DATAPLEX_CATALOG"]]
            project_id, dataset_id, table_id = self._client._utils.split_table_fqn(table_fqn)

            # Create entry name
            entry_name = f"projects/{project_id}/locations/{self._get_dataset_location(table_fqn)}/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}"
            aspect_type = f"""projects/{self._client._project_id}/locations/global/aspectTypes/{constants["ASPECT_TEMPLATE"]["name"]}"""
            aspect_name = f"""{self._client._project_id}.global.{constants["ASPECT_TEMPLATE"]["name"]}"""
            aspect_types = [aspect_type]

            # Get existing entry with aspects
            request = dataplex_v1.GetEntryRequest(
                name=entry_name,
                view=dataplex_v1.EntryView.CUSTOM,
                aspect_types=aspect_types
            )
            entry = client.get_entry(request=request)

            # Create new aspect
            new_aspect = dataplex_v1.Aspect()
            new_aspect.aspect_type = aspect_type

            # Update or create aspect data
            for i in entry.aspects:
                if i.endswith(f"""global.{constants["ASPECT_TEMPLATE"]["name"]}""") and entry.aspects[i].path=="":
                    logger.info(f"Updating existing aspect {i}")
                    new_aspect.data = entry.aspects[i].data
                    new_aspect.data.update({
                        "generation-date": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "to-be-regenerated": False
                    })
                    break
            else:
                # No existing aspect found, create new one
                aspect_data = {
                    "certified": "false",
                    "user-who-certified": "",
                    "contents": "",
                    "generation-date": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "to-be-regenerated": False,
                    "human-comments": [],
                    "negative-examples": [],
                    "external-document-uri": ""
                }
                data_struct = struct_pb2.Struct()
                data_struct.update(aspect_data)
                new_aspect.data = data_struct

            # Create new entry with updated aspect
            new_entry = dataplex_v1.Entry()
            new_entry.name = entry.name
            new_entry.aspects[aspect_name] = new_aspect

            # Update entry
            request = dataplex_v1.UpdateEntryRequest(
                entry=new_entry,
                update_mask=field_mask_pb2.FieldMask(paths=["aspects"]),
                allow_missing=False,
                aspect_keys=[aspect_name]
            )

            # Make the request
            response = client.update_entry(request=request)
            logger.info(f"Successfully marked table {table_fqn} as regenerated")
            return True

        except Exception as e:
            logger.error(f"Failed to mark table {table_fqn} as regenerated: {str(e)}")
            return False

    def mark_column_for_regeneration(self, table_fqn: str, column_name: str) -> bool:
        """Marks a column for regeneration by setting the to-be-regenerated flag in its metadata."""
        self._client._utils.validate_column_name(column_name)
        try:
            client = self._client._cloud_clients[constants["CLIENTS"]["DATAPLEX_CATALOG"]]
            project_id, dataset_id, table_id = self._client._utils.split_table_fqn(table_fqn)

            # Create entry name
            entry_name = f"projects/{project_id}/locations/{self._get_dataset_location(table_fqn)}/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}"
            aspect_type = f"""projects/{self._client._project_id}/locations/global/aspectTypes/{constants["ASPECT_TEMPLATE"]["name"]}"""
            aspect_name = f"""{self._client._project_id}.global.{constants["ASPECT_TEMPLATE"]["name"]}@Schema.{column_name}"""
            aspect_types = [aspect_type]

            # Get existing entry
            request = dataplex_v1.GetEntryRequest(
                name=entry_name,
                view=dataplex_v1.EntryView.CUSTOM,
                aspect_types=aspect_types
            )
            entry = client.get_entry(request=request)

            # Create new aspect
            new_aspect = dataplex_v1.Aspect()
            new_aspect.aspect_type = aspect_type
            new_aspect.path = f"Schema.{column_name}"

            # Update or create aspect data
            for i in entry.aspects:
                if i.endswith(f"""global.{constants["ASPECT_TEMPLATE"]["name"]}@Schema.{column_name}""") and entry.aspects[i].path == f"Schema.{column_name}":
                    logger.info(f"Updating existing aspect {i}")
                    new_aspect.data = entry.aspects[i].data
                    new_aspect.data.update({
                        "generation-date": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "to-be-regenerated": True
                    })
                    break
            else:
                # No existing aspect found, create new one
                aspect_data = {
                    "certified": "false",
                    "user-who-certified": "",
                    "contents": "",
                    "generation-date": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "to-be-regenerated": True,
                    "human-comments": [],
                    "negative-examples": [],
                    "external-document-uri": ""
                }
                data_struct = struct_pb2.Struct()
                data_struct.update(aspect_data)
                new_aspect.data = data_struct

            # Create new entry with updated aspect
            new_entry = dataplex_v1.Entry()
            new_entry.name = entry.name
            new_entry.aspects[aspect_name] = new_aspect

            # Update entry
            request = dataplex_v1.UpdateEntryRequest(
                entry=new_entry,
                update_mask=field_mask_pb2.FieldMask(paths=["aspects"]),
                allow_missing=False,
                aspect_keys=[aspect_name]
            )

            # Make the request
            response = client.update_entry(request=request)
            logger.info(f"Successfully marked column {column_name} in table {table_fqn} for regeneration")
            return True

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Failed to mark column {column_name} in table {table_fqn} for regeneration: {str(e)}")
            return False

    def mark_column_as_regenerated(self, table_fqn: str, column_name: str) -> bool:
        """Marks a column as regenerated by setting the to-be-regenerated flag to false in its metadata."""
        self._client._utils.validate_column_name(column_name)
        try:
            client = self._client._cloud_clients[constants["CLIENTS"]["DATAPLEX_CATALOG"]]
            project_id, dataset_id, table_id = self._client._utils.split_table_fqn(table_fqn)

            # Create entry name
            entry_name = f"projects/{project_id}/locations/{self._get_dataset_location(table_fqn)}/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}"
            aspect_type = f"""projects/{self._client._project_id}/locations/global/aspectTypes/{constants["ASPECT_TEMPLATE"]["name"]}"""
            aspect_name = f"""{self._client._project_id}.global.{constants["ASPECT_TEMPLATE"]["name"]}@Schema.{column_name}"""
            aspect_types = [aspect_type]

            # Get existing entry
            request = dataplex_v1.GetEntryRequest(
                name=entry_name,
                view=dataplex_v1.EntryView.CUSTOM,
                aspect_types=aspect_types
            )
            entry = client.get_entry(request=request)

            # Create new aspect
            new_aspect = dataplex_v1.Aspect()
            new_aspect.aspect_type = aspect_type
            new_aspect.path = f"Schema.{column_name}"

            # Update or create aspect data
            for i in entry.aspects:
                if i.endswith(f"""global.{constants["ASPECT_TEMPLATE"]["name"]}@Schema.{column_name}""") and entry.aspects[i].path == f"Schema.{column_name}":
                    logger.info(f"Updating existing aspect {i}")
                    new_aspect.data = entry.aspects[i].data
                    new_aspect.data.update({
                        "generation-date": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "to-be-regenerated": False
                    })
                    break
            else:
                # No existing aspect found, create new one
                aspect_data = {
                    "certified": "false",
                    "user-who-certified": "",
                    "contents": "",
                    "generation-date": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "to-be-regenerated": False,
                    "human-comments": [],
                    "negative-examples": [],
                    "external-document-uri": ""
                }
                data_struct = struct_pb2.Struct()
                data_struct.update(aspect_data)
                new_aspect.data = data_struct

            # Create new entry with updated aspect
            new_entry = dataplex_v1.Entry()
            new_entry.name = entry.name
            new_entry.aspects[aspect_name] = new_aspect

            # Update entry
            request = dataplex_v1.UpdateEntryRequest(
                entry=new_entry,
                update_mask=field_mask_pb2.FieldMask(paths=["aspects"]),
                allow_missing=False,
                aspect_keys=[aspect_name]
            )

            # Make the request
            response = client.update_entry(request=request)
            logger.info(f"Successfully marked column {column_name} in table {table_fqn} as regenerated")
            return True

        except Exception as e:
            logger.error(f"Failed to mark column {column_name} in table {table_fqn} as regenerated: {str(e)}")
            return False 