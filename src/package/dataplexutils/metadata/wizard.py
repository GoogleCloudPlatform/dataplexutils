#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Dataplex Utils Metadata Wizard main logic
   2024 Google
"""
# OS Imports
import logging
import toml
import pkgutil
import re
# Cloud imports
import vertexai
import json
from google.cloud import bigquery
from google.cloud import dataplex_v1
from google.cloud.dataplex_v1 import GetDataScanRequest, ListDataScanJobsRequest, GetDataScanJobRequest
from google.cloud import datacatalog_lineage_v1
from google.cloud.dataplex_v1.types.datascans import DataScanJob
from google.cloud.exceptions import NotFound
from vertexai.generative_models import GenerationConfig, GenerativeModel

# Load constants
constants = toml.loads(pkgutil.get_data(
    __name__, "constants.toml").decode())
# Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(constants["LOGGING"]["WIZARD_LOGGER"])


class Client:
    """Represents the main metadata wizard client.
    """
    def __init__(self, project_id: str, location: str):
        self._project_id = project_id
        self._location = location
        self._cloud_clients = {constants["CLIENTS"]
                               ["BIGQUERY"]: bigquery.Client(),
                               constants["CLIENTS"]
                               ["DATAPLEX_DATA_SCAN"]: dataplex_v1.DataScanServiceClient(),
                               constants["CLIENTS"]
                               ["DATA_CATALOG_LINEAGE"]: datacatalog_lineage_v1.LineageClient()
                               }

    def generate_table_description(self, table_fqn: str) -> None:
        """Generates metadata on the tabes.

        Args:
            table_fqn: The fully qualified name of the table (e.g., 'project.dataset.table')

        Returns:
          None.

        Raises:
            NotFound: If the specified table does not exist.
        """
        self._table_exists(table_fqn)
        table_schema = self._get_table_schema(table_fqn)
        table_sample = self._get_table_sample(
            table_fqn, constants["DATA"]["NUM_ROWS_TO_SAMPLE"])
        table_description_prompt = constants["PROMPTS"]["SYSTEM_PROMPT"] + \
            constants["PROMPTS"]["TABLE_DESCRIPTION_PROMPT"] + constants["PROMPTS"]["OUTPUT_FORMAT_PROMPT"]
        table_profile_quality = self._get_table_profile_quality(
            table_fqn)
        table_description_prompt_expanded = table_description_prompt.format(
            table_fqn, table_schema, table_sample, table_profile_quality['data_profile'], table_profile_quality['data_quality'])
        description = self._llm_inference(table_description_prompt_expanded)
        self._update_table_description(table_fqn, description)
        logger.info("Table {} description updated.".format(table_fqn))
        logger.info(f"Prompt used is {table_description_prompt_expanded}")
        logger.info(f"Description is {description}")
             
    def generate_column_description(self, table_fqn: str) -> None:
        """Generates metadata on the columns.

        Args:
            table_fqn: The fully qualified name of the table (e.g., 'project.dataset.table')

        Returns:
          None.

        Raises:
            NotFound: If the specified table does not exist.
        """
        pass

    def _table_exists(self, table_fqn: str) -> None:
        """Checks if a specified BigQuery table exists.

        Args:
            table_fqn: The fully qualified name of the table (e.g., 'project.dataset.table')

        Raises:
            NotFound: If the specified table does not exist.
        """
        try:
            self._cloud_clients[constants["CLIENTS"]
                                ["BIGQUERY"]].get_table(table_fqn)
        except NotFound:
            logger.error("Table {} is not found.".format(table_fqn))
      
    def _get_table_schema(self, table_fqn):
        try:
            table = self._cloud_clients[constants["CLIENTS"]
                                ["BIGQUERY"]].get_table(table_fqn)
            schema_fields = table.schema
            flattened_schema = [{'name': field.name,
                                 'type': field.field_type
                                 } for field in schema_fields]
            return flattened_schema
        except NotFound:
            logger.error("Table {} is not found.".format(table_fqn))
            raise NotFound(message="Table {} is not found.".format(table_fqn))

    def _get_table_sample(self, table_fqn, num_rows_to_sample):
        try:
            client = self._cloud_clients[constants["CLIENTS"]
                                            ["BIGQUERY"]]
            query = (f"SELECT * FROM {table_fqn} LIMIT {num_rows_to_sample}")
            return client.query(query).to_dataframe().to_json()
        except Exception as e:
            logger.error("Exception {}.".format(e))
            raise e

    def _split_table_fqn(self, table_fqn):
        try:
            pattern = r"^([^.]+)\.([^.]+)\.([^.]+)"
            match = re.search(pattern, table_fqn)
            return match.group(1), match.group(2), match.group(3)
        except Exception as e:
            logger.error("Exception {}.".format(e))
            raise e

    def _construct_bq_resource_string(self, table_fqn):
        try:
            project_id, dataset_id, table_id = self._split_table_fqn(table_fqn)
            return f"//bigquery.googleapis.com/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}"
        except Exception as e:
            logger.error("Exception {}.".format(e))
            raise e
 
    def _get_table_scan_reference(self, table_fqn):
        try:
            scan_reference = None
            client = self._cloud_clients[constants["CLIENTS"]
                                         ["DATAPLEX_DATA_SCAN"]]                    
            data_scans = client.list_data_scans(parent=f"projects/{self._project_id}/locations/{self._location}")            
            bq_resource_string = self._construct_bq_resource_string(table_fqn)
            scan_references=[]
            for scan in data_scans:
                if scan.data.resource == bq_resource_string:
                    scan_references.append(scan.name)
            return scan_references
        except Exception as e:
            logger.error("Exception {}.".format(e))
            raise e

    def _get_table_profile_quality(self, table_fqn):
        try:
            client = self._cloud_clients[constants["CLIENTS"]
                                        ["DATAPLEX_DATA_SCAN"]]
            data_profile_results = []
            data_quality_results = []
            table_scan_references = self._get_table_scan_reference(table_fqn)
            for table_scan_reference in table_scan_references:
                if table_scan_reference:
                    for job in client.list_data_scan_jobs(ListDataScanJobsRequest(parent=client.get_data_scan(GetDataScanRequest(
                            name=table_scan_reference)).name)):
                        job_result = client.get_data_scan_job(request=GetDataScanJobRequest(name=job.name, view="FULL"))
                        if job_result.state == DataScanJob.State.SUCCEEDED:
                            job_result_json = json.loads(
                                dataplex_v1.types.datascans.DataScanJob.to_json(job_result))
                            if "dataQualityResult" in job_result_json:
                                data_quality_results.append(job_result_json["dataQualityResult"])
                            if "dataProfileResult" in job_result_json:
                                data_profile_results.append(
                                    job_result_json["dataProfileResult"])
            return {"data_profile": data_profile_results, "data_quality": data_quality_results}
        except Exception as e:
            logger.error("Exception {}.".format(e))
            raise e

    def _llm_inference(self, prompt):
        try:
            vertexai.init(project=self._project_id, location=self._location)
            model = GenerativeModel(constants["LLM"]
                                    ["LLM_TYPE"])
            generation_config = GenerationConfig(
                temperature=constants["LLM"]
                ["TEMPERATURE"],
                top_p=constants["LLM"]
                ["TOP_P"],
                top_k=constants["LLM"]
                ["TOP_K"],
                candidate_count=constants["LLM"]
                ["CANDIDATE_COUNT"],
                max_output_tokens=constants["LLM"]
                ["MAX_OUTPUT_TOKENS"],
            )
            responses = model.generate_content(
                prompt,
                generation_config=generation_config,
                stream=False,
            )
            return responses.text
        except Exception as e:
            logger.error("Exception {}.".format(e))
            raise e

    def _update_table_description(self, table_fqn, description):
        try:
            table = self._cloud_clients[constants["CLIENTS"]
                                    ["BIGQUERY"]].get_table(table_fqn)
            table.description = description
            _ = self._cloud_clients[constants["CLIENTS"]
                                    ["BIGQUERY"]].update_table(table, ["description"])
        except Exception as e:
            logger.error("Exception {}.".format(e))
            raise e