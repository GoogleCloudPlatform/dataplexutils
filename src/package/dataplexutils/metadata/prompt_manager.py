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

from enum import Enum
import logging
import toml
import pkgutil

# Load constants from constants.toml located in the same package
constants = toml.loads(pkgutil.get_data(__package__, "constants.toml").decode())

logger = logging.getLogger(constants["LOGGING"]["WIZARD_LOGGER"])

class PromtType(Enum):
    PROMPT_TYPE_TABLE = 0
    PROMPT_TYPE_COLUMN = 1

class PromptManager:
    """Represents a prompt manager."""
    def __init__(self, prompt_type, client_options):
        self._prompt_type = prompt_type
        self._client_options = client_options

    def get_prompt(self):
        """Return the prompt template for the configured prompt type."""
        try:
            if self._prompt_type == PromtType.PROMPT_TYPE_TABLE:
                return self._get_prompt_table()
            elif self._prompt_type == PromtType.PROMPT_TYPE_COLUMN:
                return self._get_prompt_columns()
            else:
                return None
        except Exception as e:
            logger.error(f"Exception: {e}.")
            raise e

    def get_promtp(self):
        """Backward-compatible alias for get_prompt()."""
        return self.get_prompt()

    def _get_prompt_table(self):
        try:
            # Anti-injection preamble + Base
            table_description_prompt = constants["PROMPTS"]["UNTRUSTED_DATA_PREAMBLE"]
            table_description_prompt = (
                table_description_prompt
                + constants["PROMPTS"]["TABLE_DESCRIPTION_PROMPT_BASE"]
            )
            if constants["DATA"]["NUM_ROWS_TO_SAMPLE"] > 0:
                table_description_prompt = (
                    table_description_prompt
                    + constants["PROMPTS"]["TABLE_DESCRIPTION_PROMPT_SAMPLE"]
                )
            # Additional metadata information
            if self._client_options._use_profile:
                table_description_prompt = (
                    table_description_prompt
                    + constants["PROMPTS"]["TABLE_DESCRIPTION_PROMPT_PROFILE"]
                )
            if self._client_options._use_data_quality:
                table_description_prompt = (
                    table_description_prompt
                    + constants["PROMPTS"]["TABLE_DESCRIPTION_PROMPT_QUALITY"]
                )
            if self._client_options._use_lineage_tables:
                table_description_prompt = (
                    table_description_prompt
                    + constants["PROMPTS"]["TABLE_DESCRIPTION_PROMPT_LINEAGE_TABLES"]
                )
            if self._client_options._use_lineage_processes:
                table_description_prompt = (
                    table_description_prompt
                    + constants["PROMPTS"]["TABLE_DESCRIPTION_PROMPT_LINEAGE_PROCESSES"]
                )
            if self._client_options._use_ext_documents:
                table_description_prompt = (
                    table_description_prompt
                    + constants["PROMPTS"]["TABLE_DESCRIPTION_PROMPT_DOCUMENT"]
                )

            if self._client_options._use_human_comments:
                table_description_prompt = (
                    table_description_prompt
                    + constants["PROMPTS"]["TABLE_DESCRIPTION_PROMPT_HUMAN_COMMENTS"]
                )
            # Generation base
            table_description_prompt = (
                table_description_prompt
                + constants["PROMPTS"]["TABLE_DESCRIPTION_GENERATION_BASE"]
            )
            # Generation with additional information
            if (
                self._client_options._use_lineage_tables
                or self._client_options._use_lineage_processes
            ):
                table_description_prompt = (
                    table_description_prompt
                    + constants["PROMPTS"]["TABLE_DESCRIPTION_GENERATION_LINEAGE"]
                )
            # Output format
            table_description_prompt = (
                table_description_prompt + constants["PROMPTS"]["OUTPUT_FORMAT_PROMPT"]
            )

            logger.debug("Built table description prompt template.")
            return table_description_prompt
        except Exception as e:
            logger.error(f"Exception: {e}.")
            raise e

    def _get_prompt_columns(self):
        try:
            # Anti-injection preamble + Base
            column_description_prompt = constants["PROMPTS"]["UNTRUSTED_DATA_PREAMBLE"]
            if self._client_options._top_values_in_description == True:
                column_description_prompt = (
                    column_description_prompt
                    + constants["PROMPTS"]["COLUMN_DESCRIPTION_PROMPT_BASE_WITH_EXAMPLES"]
                )
            else:
                column_description_prompt = (
                    column_description_prompt
                    + constants["PROMPTS"]["COLUMN_DESCRIPTION_PROMPT_BASE"]
                )

            if constants["DATA"]["NUM_ROWS_TO_SAMPLE"] > 0:
                column_description_prompt = (
                    column_description_prompt
                    + constants["PROMPTS"]["TABLE_DESCRIPTION_PROMPT_SAMPLE"]
                )
            # Additional metadata information
            if self._client_options._use_profile:
                column_description_prompt = (
                    column_description_prompt
                    + constants["PROMPTS"]["TABLE_DESCRIPTION_PROMPT_PROFILE"]
                )
            if self._client_options._use_data_quality:
                column_description_prompt = (
                    column_description_prompt
                    + constants["PROMPTS"]["TABLE_DESCRIPTION_PROMPT_QUALITY"]
                )
            if self._client_options._use_lineage_tables:
                column_description_prompt = (
                    column_description_prompt
                    + constants["PROMPTS"]["TABLE_DESCRIPTION_PROMPT_LINEAGE_TABLES"]
                )
            if self._client_options._use_lineage_processes:
                column_description_prompt = (
                    column_description_prompt
                    + constants["PROMPTS"]["TABLE_DESCRIPTION_PROMPT_LINEAGE_PROCESSES"]
                )
            if self._client_options._use_ext_documents:
                column_description_prompt = (
                    column_description_prompt
                    + constants["PROMPTS"]["TABLE_DESCRIPTION_PROMPT_DOCUMENT"]
                )
            if self._client_options._use_human_comments:
                column_description_prompt = (
                    column_description_prompt
                    + constants["PROMPTS"]["COLUMN_DESCRIPTION_PROMPT_HUMAN_COMMENTS"]
                )
            # Output format
            column_description_prompt = (
                column_description_prompt + constants["PROMPTS"]["OUTPUT_FORMAT_PROMPT"]
            )
            logger.debug("Built column description prompt template.")
            return column_description_prompt
        except Exception as e:
            logger.error(f"Exception: {e}.")
            raise e 