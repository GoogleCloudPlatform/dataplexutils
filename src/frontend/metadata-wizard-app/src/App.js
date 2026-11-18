/*
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
*/

import React, { useState } from 'react';
import axios from 'axios';
import './styles.css';
import '@fontsource/roboto/400.css';
import {
  TextField, Button, FormControlLabel, Checkbox, Box, Select, MenuItem, InputLabel
} from '@mui/material';

const API_TOKEN_SESSION_KEY = 'metadata_wizard_api_token';

function getInitialApiToken() {
  try {
    if (typeof window !== 'undefined' && window.sessionStorage) {
      const stored = window.sessionStorage.getItem(API_TOKEN_SESSION_KEY);
      if (stored !== null) {
        return stored;
      }
    }
  } catch (e) {
    // Ignore storage errors in restricted contexts.
  }
  if (
    typeof process !== 'undefined' &&
    process.env &&
    typeof process.env.REACT_APP_API_TOKEN === 'string' &&
    process.env.REACT_APP_API_TOKEN.trim() !== ''
  ) {
    return process.env.REACT_APP_API_TOKEN.trim();
  }
  return '';
}

function saveSessionApiToken(token) {
  const normalized = typeof token === 'string' ? token.trim() : '';
  try {
    if (typeof window !== 'undefined' && window.sessionStorage) {
      if (normalized) {
        window.sessionStorage.setItem(API_TOKEN_SESSION_KEY, normalized);
      } else {
        const hasEnvToken =
          typeof process !== 'undefined' &&
          process.env &&
          typeof process.env.REACT_APP_API_TOKEN === 'string' &&
          process.env.REACT_APP_API_TOKEN.trim() !== '';
        if (hasEnvToken) {
          window.sessionStorage.setItem(API_TOKEN_SESSION_KEY, '');
        } else {
          window.sessionStorage.removeItem(API_TOKEN_SESSION_KEY);
        }
      }
    }
  } catch (e) {
    // Ignore storage errors in restricted contexts.
  }
  return normalized;
}

function App() {
  const [params, setParams] = useState({
    client_options_settings: {
      use_lineage_tables: false,
      use_lineage_processes: false,
      use_profile: false,
      use_data_quality: false,
      use_ext_documents: false,
      persist_to_dataplex_catalog: true,
      stage_for_review: true,
      dry_run: false,
      top_values_in_description: true,
      description_handling: 'append',
      description_prefix: '---AI Generated description---',
    },
    client_settings: {
      project_id: '',
      llm_location: '',
      dataplex_location: ''
      //documentation_uri: ''
    },
    table_settings: {
      project_id: '',
      dataset_id: '',
      table_id: '',
      documentation_uri: ''
    },
    dataset_settings: {
      project_id: '',
      dataset_id: '',
      documentation_csv_uri: '',
      strategy: 'NAIVE'
    },
  });

  const [apiUrlBase, setApiUrlBase] = useState(
    () => (typeof process !== 'undefined' && process.env && process.env.REACT_APP_API_URL) || 'http://localhost:8000'
  );
  const [apiToken, setApiToken] = useState(() => getInitialApiToken());
  const [apiResponse, setApiResponse] = useState(null);

  const [persistToDataplex, setPersistToDataplex] = useState(true);
  const [stageForReview, setStageForReview] = useState(true);

  const [isGenerating, setIsGenerating] = useState(false);

  const handleChange = (event) => {
    const { name, value, type, checked } = event.target;

    if (name === "apiUrlBase") {
      setApiUrlBase(value);
    } else if (name === "apiToken") {
      saveSessionApiToken(value);
      setApiToken(value);
    } else {
      const [parent, child] = name.split('.');

      setParams((prevParams) => ({
        ...prevParams,
        [parent]: {
          ...prevParams[parent],
          [child]: type === 'checkbox' ? checked : value,
        },
      }));
      if (parent === 'table_settings' && child === 'dataset_id') {
        setParams((prevParams) => ({
          ...prevParams,
          dataset_settings: {
            ...prevParams.dataset_settings,
            dataset_id: value, // Store the value in dataset_settings
          },
        }));
      }
      if (parent === 'table_settings' && child === 'project_id') {
        setParams((prevParams) => ({
          ...prevParams,
          dataset_settings: {
            ...prevParams.dataset_settings,
            project_id: value, // Store the value in dataset_settings
          },
        }));
      }
      
      if (name === 'dataset_settings.strategy') {
        setParams((prevParams) => ({
          ...prevParams,
          dataset_settings: {
            ...prevParams.dataset_settings,
            strategy: value,
          },
        }));
      }
    }
    
  };

  const handlePersistToDataplexChange = (event) => {
    setPersistToDataplex(event.target.checked);
  };

  const handleStageForReviewChange = (event) => {
    setStageForReview(event.target.checked);
  };

  const callApi = async (endpoint) => {
    try {
      const baseUrl = (apiUrlBase || 'http://localhost:8000').replace(/\/+$/, '');
      const requestBody = {
        client_options_settings: {
          use_lineage_tables: params.client_options_settings.use_lineage_tables,
          use_lineage_processes: params.client_options_settings.use_lineage_processes,
          use_profile: params.client_options_settings.use_profile,
          use_data_quality: params.client_options_settings.use_data_quality,
          use_ext_documents: params.client_options_settings.use_ext_documents,
          persist_to_dataplex_catalog: persistToDataplex,
          stage_for_review: stageForReview,
          dry_run: params.client_options_settings.dry_run,
          top_values_in_description: params.client_options_settings.top_values_in_description,
          description_handling: params.client_options_settings.description_handling,
          description_prefix: params.client_options_settings.description_prefix,
        },
        client_settings: {
          project_id: params.client_settings.project_id,
          llm_location: params.client_settings.llm_location,
          dataplex_location: params.client_settings.dataplex_location,
        },
        table_settings: {
          project_id: params.table_settings.project_id,
          dataset_id: params.table_settings.dataset_id,
          table_id: params.table_settings.table_id,
          documentation_uri: params.table_settings.documentation_uri,
        },
        dataset_settings: {
          project_id: params.dataset_settings.project_id,
          dataset_id: params.dataset_settings.dataset_id,
          documentation_csv_uri: params.dataset_settings.documentation_csv_uri,
          strategy: params.dataset_settings.strategy,
        },
      };

      const trimmedToken = (apiToken || '').trim();
      const headers = trimmedToken ? { 'X-API-Key': trimmedToken } : {};

      console.log("Sending request to:", `${baseUrl}/${endpoint}`);
      console.log("Request body:", JSON.stringify(requestBody, null, 2));

      setIsGenerating(true);
      const response = await axios.post(
        `${baseUrl}/${endpoint}`,
        requestBody,
        { headers }
      );
      setApiResponse(response.data);
      setIsGenerating(false);
    } catch (error) {
      console.error("API Error:", error);
      if (error.response) {
        console.error("Response data:", error.response.data);
        console.error("Response status:", error.response.status);
        console.error("Response headers:", error.response.headers);
      } else if (error.request) {
        console.error("Request:", error.request);
      } else {
        console.error("Error message:", error.message);
      }
      const detail = error.response?.data?.detail;
      setApiResponse({
        error: detail
          ? `API Error: ${typeof detail === 'object' ? JSON.stringify(detail) : detail}`
          : "Network Error or API issue. Check console for details.",
      });
      setIsGenerating(false);
    }
  };

  return (
    <div className="container">
      <h1 className="title">.::metadata wizard::.</h1>

      <Box className="settings-sections-container" >
        <Box className="settings-section">
          <h2>Select the additional metadata sources to use</h2>
          <div>
            <FormControlLabel
              control={<Checkbox
                name="client_options_settings.use_lineage_tables"
                checked={params.client_options_settings.use_lineage_tables}
                onChange={handleChange}
              />
              }
              label="Use Lineage Tables"
            />
          </div>
          <div>
            <FormControlLabel
              control={<Checkbox
                name="client_options_settings.use_lineage_processes"
                checked={params.client_options_settings.use_lineage_processes}
                onChange={handleChange}
              />
              }
              label="Use Lineage Processes"
            />
          </div>
          <div>
            <FormControlLabel
              control={<Checkbox
                name="client_options_settings.use_profile"
                checked={params.client_options_settings.use_profile}
                onChange={handleChange}
              />
              }
              label="Use Profile"
            />
          </div>
          <div>
            <FormControlLabel
              control={<Checkbox
                name="client_options_settings.use_data_quality"
                checked={params.client_options_settings.use_data_quality}
                onChange={handleChange}
              />
              }
              label="Use Data Quality"
            />
          </div>
          <div>
            <FormControlLabel
              control={<Checkbox
                name="client_options_settings.use_ext_documents"
                checked={params.client_options_settings.use_ext_documents}
                onChange={handleChange}
              />
              }
              label="Use External Documents"
            />
          </div>
          <div>
          <InputLabel id="strategy-select-label">Dataset Strategy</InputLabel>
          <Select
            labelId="strategy-select-label"
            id="dataset_settings.strategy"
            name="dataset_settings.strategy"
            value={params.dataset_settings.strategy}
            onChange={handleChange}
            fullWidth
          >
            <MenuItem value="NAIVE">Naive</MenuItem>
            <MenuItem value="DOCUMENTED">Documented</MenuItem>
            <MenuItem value="DOCUMENTED_THEN_REST">Documented then rest</MenuItem>
          </Select>
        </div>
          
        </Box>

        <Box className="settings-section">
          <h2>Enter LLM / Dataplex project details</h2>
          <div>
            <TextField
              label="Project ID"
              id="client_settings.project_id"
              name="client_settings.project_id"
              value={params.client_settings.project_id}
              onChange={handleChange}
              fullWidth
            />
          </div>
          <div>
            <TextField
              label="LLM Location"
              id="client_settings.llm_location"
              name="client_settings.llm_location"
              value={params.client_settings.llm_location}
              onChange={handleChange}
              fullWidth
            />
          </div>
          <div>
            <TextField
              label="Dataplex Location"
              id="client_settings.dataplex_location"
              name="client_settings.dataplex_location"
              value={params.client_settings.dataplex_location}
              onChange={handleChange}
              fullWidth
            />
          </div>
          <div>
            <TextField
              label="Table Documentation URI"
              id="table_settings.documentation_uri"
              name="table_settings.documentation_uri"
              value={params.table_settings.documentation_uri}
              onChange={handleChange}
              fullWidth
            />
          </div>
          <div>
            <TextField
              label="Dataset Documentation CSV URI"
              id="dataset_settings.documentation_csv_uri"
              name="dataset_settings.documentation_csv_uri"
              value={params.dataset_settings.documentation_csv_uri}
              onChange={handleChange}
              fullWidth
            />
          </div>
        </Box>

        <Box className="settings-section">
          <h2>Enter table to generate metadata</h2>
          <div>
            <TextField
              label="Project ID"
              id="table_settings.project_id"
              name="table_settings.project_id"
              value={params.table_settings.project_id}
              onChange={handleChange}
              fullWidth
            />
          </div>
          <div>
            <TextField
              label="Dataset ID"
              id="table_settings.dataset_id"
              name="table_settings.dataset_id"
              value={params.table_settings.dataset_id}
              onChange={handleChange}
              fullWidth
            />
          </div>
          <div>
            <TextField
              label="Table ID"
              id="table_settings.table_id"
              name="table_settings.table_id"
              value={params.table_settings.table_id}
              onChange={handleChange}
              fullWidth
            />
          </div>
        </Box>

        <Box className="settings-section">
          <h2>API backend URL &amp; Auth</h2>
          <div>
            <TextField
              label="API Base URL"
              name="apiUrlBase"
              value={apiUrlBase}
              onChange={handleChange}
              fullWidth
            />
          </div>
          <div>
            <TextField
              label="API Token (X-API-Key)"
              name="apiToken"
              type="password"
              value={apiToken}
              onChange={handleChange}
              autoComplete="off"
              helperText="Stored in sessionStorage only for this session"
              fullWidth
            />
          </div>
        </Box>
      </Box>

      <Box sx={{ display: 'flex', gap: 4, justifyContent: 'center' }}>
        <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
          <h3>Generate for single table</h3>
          <Button variant="contained" onClick={() => callApi('generate_table_description')}>
            Table Description
          </Button>
          <Button variant="contained" onClick={() => callApi('generate_columns_descriptions')}>
            Column Descriptions
          </Button>
        </Box>

        <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
          <h3>Generate for dataset</h3>
          <Button variant="contained" onClick={() => callApi('generate_dataset_tables_descriptions')}>
            All Tables in Dataset
          </Button>
          <Button variant="contained" onClick={() => callApi('generate_dataset_tables_columns_descriptions')}>
            All Tables and Columns
          </Button>
        </Box>
      </Box>

      <Box className="settings-section">
        <h2>Additional Options</h2>
        <FormControlLabel
          control={<Checkbox checked={persistToDataplex} onChange={handlePersistToDataplexChange} />}
          label="Persist Table description to Dataplex Catalog"
        />
        <FormControlLabel
          control={<Checkbox 
            checked={stageForReview} onChange={handleStageForReviewChange} />}
          label="Stage generations in Dataplex for Review"
        />
        <FormControlLabel
          control={<Checkbox
            name="client_options_settings.dry_run"
            checked={params.client_options_settings.dry_run}
            onChange={handleChange}
          />}
          label="Dry Run (Preview Only - No Metadata Writes)"
        />
        <FormControlLabel
          control={<Checkbox
            name="client_options_settings.top_values_in_description"
            checked={params.client_options_settings.top_values_in_description}
            onChange={handleChange}
          />}
          label="Store top 10 values in column descriptions"
        />
        <div>
          <InputLabel id="description-handling-label">Description Handling</InputLabel>
          <Select
            labelId="description-handling-label"
            id="client_options_settings.description_handling"
            name="client_options_settings.description_handling"
            value={params.client_options_settings.description_handling}
            onChange={handleChange}
            fullWidth
          >
            <MenuItem value="append">Append</MenuItem>
            <MenuItem value="prepend">Prepend</MenuItem>
            <MenuItem value="replace">Replace</MenuItem>
          </Select>
        </div>
        <div style={{ marginTop: '12px' }}>
          <TextField
            label="Description Prefix"
            name="client_options_settings.description_prefix"
            value={params.client_options_settings.description_prefix}
            onChange={handleChange}
            fullWidth
          />
        </div>
      </Box>

      {(isGenerating || apiResponse) && (
        <div className="response-section">
          <h2>API Response:</h2>
          {isGenerating ? (
            <div>Generating...</div>
          ) : (
            <pre>{JSON.stringify(apiResponse, null, 2)}</pre>
          )}
        </div>
      )}
    </div>
  );
}

export default App;
