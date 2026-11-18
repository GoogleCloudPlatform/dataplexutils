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


import toml
import pkgutil
import json

# Load constants from constants.toml located in the same package
constants = toml.loads(pkgutil.get_data(__package__, "constants.toml").decode())

class ClientOptions:
    """Represents the client options for the metadata wizard client."""
    def __init__(
        self,
        use_lineage_tables=False,
        use_lineage_processes=False,
        use_profile=False,
        use_data_quality=False,
        use_ext_documents=False,
        persist_to_dataplex_catalog=True,
        stage_for_review=True,
        add_ai_warning=True,
        use_human_comments=False,
        regenerate=False,
        top_values_in_description=True,
        description_handling=constants["DESCRIPTION_HANDLING"]["APPEND"],
        description_prefix=constants["OUTPUT_CLAUSES"]["AI_WARNING"],
        dry_run=False,
        confirm_overwrite=False,
    ):
        self._use_lineage_tables = bool(use_lineage_tables)
        self._use_lineage_processes = bool(use_lineage_processes)
        self._use_profile = bool(use_profile)
        self._use_data_quality = bool(use_data_quality)
        self._use_ext_documents = bool(use_ext_documents)
        self._persist_to_dataplex_catalog = bool(persist_to_dataplex_catalog)
        self._stage_for_review = bool(stage_for_review)
        self._add_ai_warning = bool(add_ai_warning)
        self._use_human_comments = bool(use_human_comments)
        self._regenerate = bool(regenerate)
        self._top_values_in_description = bool(top_values_in_description)
        self._description_handling = description_handling
        self._description_prefix = description_prefix
        self._dry_run = bool(dry_run)
        self._confirm_overwrite = bool(confirm_overwrite)
        
    def to_dict(self):
        """Convert the ClientOptions object to a dictionary."""
        return {
            "use_lineage_tables": self._use_lineage_tables,
            "use_lineage_processes": self._use_lineage_processes,
            "use_profile": self._use_profile,
            "use_data_quality": self._use_data_quality,
            "use_ext_documents": self._use_ext_documents,
            "persist_to_dataplex_catalog": self._persist_to_dataplex_catalog,
            "stage_for_review": self._stage_for_review,
            "add_ai_warning": self._add_ai_warning,
            "use_human_comments": self._use_human_comments,
            "regenerate": self._regenerate,
            "top_values_in_description": self._top_values_in_description,
            "description_handling": self._description_handling,
            "description_prefix": self._description_prefix,
            "dry_run": self._dry_run,
            "confirm_overwrite": self._confirm_overwrite,
        }
    
    def __str__(self):
        """Return a JSON string representation of the ClientOptions object."""
        return json.dumps(self.to_dict(), indent=2)
    
    def __repr__(self):
        """Return a string representation of the ClientOptions object for debugging."""
        return self.__str__() 