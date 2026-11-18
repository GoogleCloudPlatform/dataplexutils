/*
Copyright 2025 Google LLC

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
*/

import React, { useState, ChangeEvent, useEffect } from 'react';
import {
  Box,
  Paper,
  Typography,
  TextField,
  FormControlLabel,
  Checkbox,
  Button,
  Grid,
  Select,
  MenuItem,
  InputLabel,
  FormControl,
  Divider,
  Alert,
  SelectChangeEvent,
  RadioGroup,
  Radio,
  Card,
  CardContent,
} from '@mui/material';
import axios from 'axios';
import AutorenewIcon from '@mui/icons-material/Autorenew';
import FilterAltIcon from '@mui/icons-material/FilterAlt';
import { Task } from '../components/TaskTracker';
import { v4 as uuidv4 } from 'uuid';
import { DatplexConfig } from '../App';
import {
  extractApiErrorMessage,
  getApiBaseUrl,
  getAxiosRequestConfig,
  getStoredApiToken,
} from '../utils/apiClient';

interface ClientOptionsSettings {
  use_lineage_tables: boolean;
  use_lineage_processes: boolean;
  use_profile: boolean;
  use_data_quality: boolean;
  use_ext_documents: boolean;
  persist_to_dataplex_catalog: boolean;
  stage_for_review: boolean;
  dry_run: boolean;
  top_values_in_description: boolean;
  description_handling: 'append' | 'replace' | 'prepend';
  description_prefix: string;
}

interface ClientSettings {
  project_id: string;
  llm_location: string;
  dataplex_location: string;
}

interface TableSettings {
  project_id: string;
  dataset_id: string;
  table_id: string;
  documentation_uri: string;
}

interface DatasetSettings {
  project_id: string;
  dataset_id: string;
  documentation_csv_uri: string;
  strategy: string;
}

interface RegenerationCounts {
  tables: number;
  columns: number;
}

interface GenerationPageProps {
  config: DatplexConfig;
  apiToken?: string;
  onTaskAdd: (task: Task) => void;
  onTaskUpdate: (taskId: string, updates: Partial<Task>) => void;
  onConfigChange: (config: DatplexConfig) => void;
}

const GenerationPage: React.FC<GenerationPageProps> = ({
  config,
  apiToken,
  onTaskAdd,
  onTaskUpdate,
  onConfigChange,
}) => {
  const [apiUrlBase, setApiUrlBase] = useState<string>('');
  const [apiResponse, setApiResponse] = useState<any>(null);
  const [isGenerating, setIsGenerating] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [regenerationCounts, setRegenerationCounts] = useState<RegenerationCounts>({ tables: 0, columns: 0 });
  const [isLoadingCounts, setIsLoadingCounts] = useState(false);
  const [selectedForRegeneration, setSelectedForRegeneration] = useState<string[]>([]);

  useEffect(() => {
    // Initialize API URL
    setApiUrlBase(getApiBaseUrl());
  }, []);

  const buildClientOptionsSettings = (): ClientOptionsSettings => ({
    use_lineage_tables: config.use_lineage_tables,
    use_lineage_processes: config.use_lineage_processes,
    use_profile: config.use_profile,
    use_data_quality: config.use_data_quality,
    use_ext_documents: config.use_ext_documents,
    persist_to_dataplex_catalog: config.persist_to_dataplex_catalog,
    stage_for_review: config.stage_for_review,
    dry_run: Boolean(config.dry_run),
    top_values_in_description: config.top_values_in_description,
    description_handling: config.description_handling as ClientOptionsSettings['description_handling'],
    description_prefix: config.description_prefix,
  });

  const normalizedBaseUrl = () => (apiUrlBase || getApiBaseUrl()).replace(/\/+$/, '');

  const handleInputChange = (event: ChangeEvent<HTMLInputElement>) => {
    const { name, value, type, checked } = event.target;
    const [parent, child] = name.split('.');

    const newConfig = { ...config };
    if (parent === 'client_options_settings') {
      // These properties are directly on the config object, not nested
      if (type === 'checkbox') {
        newConfig[child] = checked;
      } else {
        newConfig[child] = value;
      }
    } else if (parent === 'table_settings') {
      // Handle table settings
      if (child === 'project_id') {
        newConfig.project_id = value;
      } else if (child === 'dataset_id') {
        newConfig.dataset_id = value;
      } else if (child === 'table_id') {
        newConfig.table_id = value;
      } else if (child === 'documentation_uri') {
        newConfig.documentation_uri = value;
      }
    } else if (parent === 'dataset_settings') {
      // Handle dataset settings
      if (child === 'documentation_csv_uri') {
        newConfig.documentation_csv_uri = value;
      }
    }
    onConfigChange(newConfig);
  };

  const handleSelectChange = (event: SelectChangeEvent<string>) => {
    const { name, value } = event.target;
    const [parent, child] = name.split('.');

    const newConfig = { ...config };
    if (parent === 'dataset_settings' && child === 'strategy') {
      newConfig.strategy = value;
    }
    onConfigChange(newConfig);
  };

  const callApi = async (endpoint: string) => {
    const taskId = uuidv4();
    
    // Determine scope and create description based on endpoint
    let scope: Task['details']['scope'];
    let description: string;
    
    switch (endpoint) {
      case 'generate_table_description':
        scope = 'table';
        description = 'table description';
        break;
      case 'generate_columns_descriptions':
        scope = 'column';
        description = 'column descriptions';
        break;
      case 'generate_dataset_tables_descriptions':
        scope = 'dataset_tables';
        description = 'dataset tables descriptions';
        break;
      case 'generate_dataset_tables_columns_descriptions':
        scope = 'dataset_all';
        description = 'dataset tables and columns';
        break;
      default:
        scope = 'table';
        description = endpoint.replace(/_/g, ' ');
    }

    const task: Task = {
      id: taskId,
      type: 'generate',
      action: endpoint,
      status: 'running',
      timestamp: new Date(),
      details: {
        project: config.project_id || undefined,
        dataset: config.dataset_id || undefined,
        table: config.table_id || undefined,
        scope,
        description,
      }
    };
    onTaskAdd(task);

    try {
      setError(null);
      setIsGenerating(true);

      const requestPayload = {
        client_settings: {
          project_id: config.project_id,
          llm_location: config.llm_location,
          dataplex_location: config.dataplex_location,
        },
        client_options_settings: buildClientOptionsSettings(),
        table_settings: {
          project_id: config.project_id,
          dataset_id: config.dataset_id,
          table_id: config.table_id,
          documentation_uri: config.documentation_uri,
        },
        dataset_settings: {
          project_id: config.project_id,
          dataset_id: config.dataset_id,
          documentation_csv_uri: config.documentation_csv_uri,
          strategy: config.strategy
        }
      };

      console.log('Sending request with payload:', requestPayload);

      const response = await axios.post(
        `${normalizedBaseUrl()}/${endpoint}`,
        requestPayload,
        getAxiosRequestConfig(apiToken)
      );

      console.log('Received response:', response.data);
      setApiResponse(response.data);
      onTaskUpdate(taskId, { status: 'completed' });
    } catch (error) {
      console.error('API Error:', error);
      const errorMessage = `API Error: ${extractApiErrorMessage(error, 'An error occurred while calling the API.')}`;
      
      setError(errorMessage);
      onTaskUpdate(taskId, { 
        status: 'failed',
        error: errorMessage
      });
    } finally {
      setIsGenerating(false);
    }
  };

  const handleGetRegenerationCounts = async () => {
    const taskId = uuidv4();
    const task: Task = {
      id: taskId,
      type: 'regenerate',
      action: 'get_regeneration_counts',
      status: 'running',
      timestamp: new Date(),
      details: {
        project: config.project_id || undefined,
        dataset: config.dataset_id || undefined,
        scope: 'dataset_all',
        description: 'get regeneration counts',
      }
    };
    onTaskAdd(task);

    try {
      setIsLoadingCounts(true);
      setError(null);
      
      // Create request payload with all necessary settings
      const requestPayload = {
        client_settings: {
          project_id: config.project_id,
          llm_location: config.llm_location,
          dataplex_location: config.dataplex_location,
        },
        dataset_settings: {
          project_id: config.project_id,
          dataset_id: config.dataset_id,
          documentation_csv_uri: config.documentation_csv_uri,
          strategy: config.strategy
        }
      };
      
      console.log('Sending regeneration counts request with payload:', requestPayload);
      
      const response = await axios.post(
        `${normalizedBaseUrl()}/get_regeneration_counts`,
        requestPayload,
        getAxiosRequestConfig(apiToken)
      );
      
      // Update regeneration counts from response
      setRegenerationCounts({
        tables: response.data.tables,
        columns: response.data.columns
      });
      
      onTaskUpdate(taskId, { status: 'completed' });
    } catch (error) {
      console.error('API Error:', error);
      const detail = extractApiErrorMessage(error, '');
      const countsErrorMessage = detail
        ? `Failed to fetch regeneration counts: ${detail}`
        : 'Failed to fetch regeneration counts.';
      
      setError(countsErrorMessage);
      onTaskUpdate(taskId, { 
        status: 'failed',
        error: countsErrorMessage
      });
    } finally {
      setIsLoadingCounts(false);
    }
  };

  const handleRegenerateAll = async () => {
    const taskId = uuidv4();
    const task: Task = {
      id: taskId,
      type: 'regenerate',
      action: 'regenerate_all',
      status: 'running',
      timestamp: new Date(),
      details: {
        project: config.project_id || undefined,
        scope: 'dataset_all',
        description: 'all marked objects',
      }
    };
    onTaskAdd(task);

    try {
      setError(null);
      const response = await axios.post(
        `${normalizedBaseUrl()}/regenerate_all`,
        {
          client_settings: {
            project_id: config.project_id,
            llm_location: config.llm_location,
            dataplex_location: config.dataplex_location,
          },
          client_options_settings: buildClientOptionsSettings(),
          dataset_settings: {
            project_id: config.project_id,
            dataset_id: config.dataset_id,
            documentation_csv_uri: config.documentation_csv_uri,
            strategy: config.strategy
          }
        },
        getAxiosRequestConfig(apiToken)
      );
      if (response?.data) {
        setApiResponse(response.data);
      }
      onTaskUpdate(taskId, { status: 'completed' });
      // Refresh counts after regeneration
      handleGetRegenerationCounts();
    } catch (error) {
      console.error('API Error:', error);
      const detail = extractApiErrorMessage(error, 'Failed to trigger regeneration.');
      const errorMessage = detail.startsWith('Failed') ? detail : `Failed to trigger regeneration: ${detail}`;
      setError(errorMessage);
      onTaskUpdate(taskId, { 
        status: 'failed',
        error: errorMessage
      });
    }
  };

  const handleRegenerateSelected = async () => {
    const taskId = uuidv4();
    // Get just the table name or pattern without project and dataset
    const tablePattern = selectedForRegeneration.length > 0 ? selectedForRegeneration[0] : '';
      
    const task: Task = {
      id: taskId,
      type: 'regenerate',
      action: 'regenerate_selected',
      status: 'running',
      timestamp: new Date(),
      details: {
        project: config.project_id || undefined,
        dataset: config.dataset_id || undefined,
        scope: 'dataset_all',
        description: `selected objects (pattern: ${tablePattern})`,
      }
    };
    onTaskAdd(task);

    try {
      setError(null);
      const response = await axios.post(
        `${normalizedBaseUrl()}/regenerate_selected`,
        {
          client_settings: {
            project_id: config.project_id,
            llm_location: config.llm_location,
            dataplex_location: config.dataplex_location,
          },
          client_options_settings: buildClientOptionsSettings(),
          dataset_settings: {
            project_id: config.project_id,
            dataset_id: config.dataset_id,
            documentation_csv_uri: config.documentation_csv_uri,
            strategy: config.strategy
          },
          regeneration_request: {
            objects: [tablePattern]
          }
        },
        getAxiosRequestConfig(apiToken)
      );
      if (response?.data) {
        setApiResponse(response.data);
      }
      onTaskUpdate(taskId, { status: 'completed' });
      // Refresh counts after regeneration
      handleGetRegenerationCounts();
    } catch (error) {
      console.error('API Error:', error);
      const detail = extractApiErrorMessage(error, 'Failed to trigger selective regeneration.');
      const errorMessage = detail.startsWith('Failed') ? detail : `Failed to trigger selective regeneration: ${detail}`;
      setError(errorMessage);
      onTaskUpdate(taskId, { 
        status: 'failed',
        error: errorMessage
      });
    }
  };

  return (
    <Box>
      <Typography variant="h1" gutterBottom>
        Metadata Generation 
      </Typography>

      <Grid container spacing={3}>
        <Grid item xs={12} md={6}>
          <Paper sx={{ p: 3, mb: 3 }}>
            <Typography variant="h2" gutterBottom>
              Metadata Sources
            </Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
              <FormControlLabel
                control={
                  <Checkbox
                    name="client_options_settings.use_lineage_tables"
                    checked={config.use_lineage_tables}
                    onChange={handleInputChange}
                  />
                }
                label="Use Lineage Tables"
              />
              <FormControlLabel
                control={
                  <Checkbox
                    name="client_options_settings.use_lineage_processes"
                    checked={config.use_lineage_processes}
                    onChange={handleInputChange}
                  />
                }
                label="Use Lineage Processes"
              />
              <FormControlLabel
                control={
                  <Checkbox
                    name="client_options_settings.use_profile"
                    checked={config.use_profile}
                    onChange={handleInputChange}
                  />
                }
                label="Use Profile"
              />
              <FormControlLabel
                control={
                  <Checkbox
                    name="client_options_settings.use_data_quality"
                    checked={config.use_data_quality}
                    onChange={handleInputChange}
                  />
                }
                label="Use Data Quality"
              />
              <FormControlLabel
                control={
                  <Checkbox
                    name="client_options_settings.use_ext_documents"
                    checked={config.use_ext_documents}
                    onChange={handleInputChange}
                  />
                }
                label="Use External Documents"
              />
            </Box>
          </Paper>

          <Paper sx={{ p: 3, mb: 3 }}>
            <Typography variant="h2" gutterBottom>
              Description Handling
            </Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <FormControl>
                <Typography variant="subtitle2" gutterBottom>
                  Description Placement
                </Typography>
                <RadioGroup
                  name="client_options_settings.description_handling"
                  value={config.description_handling}
                  onChange={handleInputChange}
                >
                  <FormControlLabel
                    value="append"
                    control={<Radio />}
                    label="Append to existing description"
                  />
                  <FormControlLabel
                    value="prepend"
                    control={<Radio />}
                    label="Write at the beginning"
                  />
                  <FormControlLabel
                    value="replace"
                    control={<Radio />}
                    label="Replace existing description"
                  />
                </RadioGroup>
              </FormControl>

              <FormControl>
                <Typography variant="subtitle2" gutterBottom>
                  Description Prefix
                </Typography>
                <TextField
                  name="client_options_settings.description_prefix"
                  value={config.description_prefix}
                  onChange={handleInputChange}
                  placeholder="Enter description prefix"
                  size="small"
                  fullWidth
                  helperText="This text will be added before the generated description"
                />
              </FormControl>

              {config.description_handling === 'replace' && (
                <Alert severity="warning">
                  Replace mode overwrites existing descriptions and may be blocked by server policy unless explicitly enabled.
                </Alert>
              )}
            </Box>
          </Paper>

          <Paper sx={{ p: 3 }}>
            <Typography variant="h2" gutterBottom>
              Additional Options
            </Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
              <FormControlLabel
                control={
                  <Checkbox
                    name="client_options_settings.persist_to_dataplex_catalog"
                    checked={config.persist_to_dataplex_catalog}
                    onChange={handleInputChange}
                  />
                }
                label="Persist to Dataplex Catalog"
              />
              <FormControlLabel
                control={
                  <Checkbox
                    name="client_options_settings.stage_for_review"
                    checked={config.stage_for_review}
                    onChange={handleInputChange}
                  />
                }
                label="Stage for Review"
              />
              <FormControlLabel
                control={
                  <Checkbox
                    name="client_options_settings.dry_run"
                    checked={Boolean(config.dry_run)}
                    onChange={handleInputChange}
                  />
                }
                label="Dry Run (Preview Only - No Metadata Writes)"
              />
              <FormControlLabel
                control={
                  <Checkbox
                    name="client_options_settings.top_values_in_description"
                    checked={config.top_values_in_description}
                    onChange={handleInputChange}
                  />
                }
                label="Include Top Values in Description"
              />
              {!config.stage_for_review && !config.dry_run && (
                <Alert severity="warning" sx={{ mt: 1 }}>
                  Stage for Review is disabled; direct BigQuery metadata writes may be rejected by backend security policy.
                </Alert>
              )}
            </Box>
          </Paper>
        </Grid>

        <Grid item xs={12} md={6}>
          <Paper sx={{ p: 3, mb: 3 }}>
            <Typography variant="h2" gutterBottom>
              Table Configuration
            </Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <TextField
                label="Project ID"
                name="table_settings.project_id"
                value={config.project_id}
                onChange={handleInputChange}
                fullWidth
              />
              <TextField
                label="Dataset ID"
                name="table_settings.dataset_id"
                value={config.dataset_id}
                onChange={handleInputChange}
                fullWidth
              />
              <TextField
                label="Table ID"
                name="table_settings.table_id"
                value={config.table_id}
                onChange={handleInputChange}
                fullWidth
              />
              <TextField
                label="Documentation URI"
                name="table_settings.documentation_uri"
                value={config.documentation_uri}
                onChange={handleInputChange}
                fullWidth
              />
            </Box>
          </Paper>

          <Paper sx={{ p: 3, mb: 3 }}>
            <Typography variant="h2" gutterBottom>
              Dataset Configuration
            </Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <TextField
                label="Documentation CSV URI"
                name="dataset_settings.documentation_csv_uri"
                value={config.documentation_csv_uri}
                onChange={handleInputChange}
                fullWidth
              />
              <FormControl fullWidth>
                <InputLabel id="strategy-label">Strategy</InputLabel>
                <Select
                  labelId="strategy-label"
                  name="dataset_settings.strategy"
                  value={config.strategy}
                  onChange={handleSelectChange}
                  label="Strategy"
                >
                  <MenuItem value="NAIVE">Naive</MenuItem>
                  <MenuItem value="DOCUMENTED">Documented</MenuItem>
                  <MenuItem value="DOCUMENTED_THEN_REST">Documented Then Rest</MenuItem>
                </Select>
              </FormControl>
            </Box>
          </Paper>

          <Paper sx={{ p: 3 }}>
            <Typography variant="h2" gutterBottom>
              API Configuration
            </Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <TextField
                label="API Base URL"
                value={apiUrlBase}
                onChange={(e) => setApiUrlBase(e.target.value)}
                fullWidth
              />
              <Alert
                severity={(apiToken !== undefined ? apiToken : getStoredApiToken()).trim() ? 'info' : 'warning'}
              >
                {(apiToken !== undefined ? apiToken : getStoredApiToken()).trim()
                  ? 'API key (X-API-Key) is configured for this session.'
                  : 'No API key configured. Set your X-API-Key token on the Configuration page before calling protected endpoints.'}
              </Alert>
            </Box>
          </Paper>
        </Grid>

        {/* Generation Actions Panel */}
        <Grid item xs={12}>
          <Paper sx={{ p: 3, mb: 3 }}>
            <Typography variant="h2" gutterBottom>
              Generation Actions
            </Typography>
            <Box sx={{ display: 'flex', gap: 2, mb: 3 }}>
              <Button
                variant="contained"
                onClick={() => callApi('generate_table_description')}
                disabled={isGenerating}
                sx={{ flex: 1 }}
              >
                Generate Table Description
              </Button>
              <Button
                variant="contained"
                onClick={() => callApi('generate_columns_descriptions')}
                disabled={isGenerating}
                sx={{ flex: 1 }}
              >
                Generate Column Descriptions
              </Button>
              <Button
                variant="contained"
                onClick={() => callApi('generate_dataset_tables_descriptions')}
                disabled={isGenerating}
                sx={{ flex: 1 }}
              >
                Generate Dataset Tables
              </Button>
              <Button
                variant="contained"
                onClick={() => callApi('generate_dataset_tables_columns_descriptions')}
                disabled={isGenerating}
                sx={{ flex: 1 }}
              >
                Generate Dataset Tables and Columns
              </Button>
            </Box>

            {error && !error.includes('regeneration') && (
              <Alert severity="error" sx={{ mb: 3 }}>
                {error}
              </Alert>
            )}

            {isGenerating && (
              <Alert severity="info" sx={{ mb: 3 }}>
                Generating metadata...
              </Alert>
            )}

            {apiResponse && !('regeneration_counts' in apiResponse) && (
              apiResponse.dry_run === true || apiResponse.preview || apiResponse.dry_run_preview ? (() => {
                const previewData =
                  apiResponse.preview && typeof apiResponse.preview === 'object'
                    ? apiResponse.preview
                    : apiResponse.dry_run_preview && typeof apiResponse.dry_run_preview === 'object'
                      ? apiResponse.dry_run_preview
                      : apiResponse;
                return (
                  <Box sx={{ mb: 3 }}>
                    <Alert severity="info" sx={{ mb: 2 }}>
                      Dry Run Preview — No BigQuery or Dataplex metadata was modified.
                      {apiResponse.message ? ` ${apiResponse.message}` : ''}
                    </Alert>
                    <Paper variant="outlined" sx={{ p: 2, backgroundColor: '#fafafa' }}>
                      <Typography variant="subtitle1" gutterBottom>
                        Dry-Run Result
                      </Typography>
                      {previewData.table_fqn && (
                        <Typography variant="body2" sx={{ mb: 1 }}>
                          <strong>Table:</strong> {String(previewData.table_fqn)}
                        </Typography>
                      )}
                      {previewData.previous_description !== undefined && (
                        <Box sx={{ mb: 1 }}>
                          <Typography variant="body2" fontWeight="bold">
                            Previous Description:
                          </Typography>
                          <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
                            {previewData.previous_description || '(empty)'}
                          </Typography>
                        </Box>
                      )}
                      {previewData.proposed_description !== undefined && (
                        <Box sx={{ mb: 1 }}>
                          <Typography variant="body2" fontWeight="bold">
                            Proposed Description:
                          </Typography>
                          <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
                            {previewData.proposed_description || '(empty)'}
                          </Typography>
                        </Box>
                      )}
                      <Box
                        component="pre"
                        sx={{
                          m: 0,
                          mt: 1,
                          p: 1.5,
                          backgroundColor: '#f0f0f0',
                          borderRadius: 1,
                          overflow: 'auto',
                          maxHeight: 320,
                          fontSize: '0.8rem',
                        }}
                      >
                        {JSON.stringify(previewData, null, 2)}
                      </Box>
                    </Paper>
                  </Box>
                );
              })() : (apiResponse.status === 'success' || apiResponse.message) ? (
                <Alert severity="success" sx={{ mb: 3 }}>
                  {apiResponse.message || 'Generation completed successfully.'}
                </Alert>
              ) : null
            )}
          </Paper>
        </Grid>

        {/* Regeneration Panel */}
        <Grid item xs={12}>
          <Paper sx={{ p: 3, mb: 3, backgroundColor: '#f8f9fa' }}>
            <Typography variant="h2" gutterBottom sx={{ color: 'primary.main' }}>
              Regeneration
            </Typography>
            <Grid container spacing={3}>
              <Grid item xs={12} md={4}>
                <Box>
                  <Button
                    variant="contained"
                    onClick={handleGetRegenerationCounts}
                    disabled={isLoadingCounts}
                    startIcon={<AutorenewIcon />}
                    fullWidth
                  >
                    {selectedForRegeneration.length > 0 && selectedForRegeneration[0] 
                      ? "Get Filtered Regeneration Count" 
                      : "Get Objects for Regeneration Count"}
                  </Button>
                  
                  <Card sx={{ mt: 2 }}>
                    <CardContent>
                      <Typography variant="h6" gutterBottom>
                        {selectedForRegeneration.length > 0 && selectedForRegeneration[0]
                          ? "Filtered objects for regeneration:"
                          : "Objects marked for regeneration:"}
                      </Typography>
                      <Box sx={{ display: 'flex', justifyContent: 'space-around', mt: 2 }}>
                        <Box sx={{ textAlign: 'center' }}>
                          <Typography variant="h4" color="primary">
                            {regenerationCounts.tables}
                          </Typography>
                          <Typography variant="body2" color="text.secondary">
                            Tables
                          </Typography>
                        </Box>
                        <Box sx={{ textAlign: 'center' }}>
                          <Typography variant="h4" color="primary">
                            {regenerationCounts.columns}
                          </Typography>
                          <Typography variant="body2" color="text.secondary">
                            Columns
                          </Typography>
                        </Box>
                      </Box>
                    </CardContent>
                  </Card>
                </Box>

                {error && error.includes('regeneration') && (
                  <Alert severity="error" sx={{ mt: 2 }}>
                    {error}
                  </Alert>
                )}

                {isLoadingCounts && (
                  <Alert severity="info" sx={{ mt: 2 }}>
                    Fetching regeneration counts...
                  </Alert>
                )}
              </Grid>

              <Grid item xs={12} md={8}>
                <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                  <Box sx={{ display: 'flex', gap: 2 }}>
                    <Button
                      variant="contained"
                      onClick={handleRegenerateAll}
                      disabled={regenerationCounts.tables === 0 && regenerationCounts.columns === 0}
                      startIcon={<AutorenewIcon />}
                      sx={{ flex: 1 }}
                    >
                      Regenerate All
                    </Button>
                    
                    <Button
                      variant="contained"
                      onClick={handleRegenerateSelected}
                      disabled={selectedForRegeneration.length === 0}
                      startIcon={<FilterAltIcon />}
                      sx={{ flex: 1 }}
                    >
                      Regenerate Selected
                    </Button>
                  </Box>

                  <TextField
                    label="Filter Pattern (e.g., table_name or *pattern*)"
                    fullWidth
                    onChange={(e) => {
                      // Only set non-empty values or empty array
                      if (e.target.value.trim()) {
                        setSelectedForRegeneration([e.target.value.trim()]);
                      } else {
                        setSelectedForRegeneration([]);
                      }
                    }}
                    helperText="Enter a table name or pattern with * wildcard. You don't need to include project/dataset - they're added automatically."
                  />
                  
                  {selectedForRegeneration.length > 0 && selectedForRegeneration[0] && (
                    <Alert severity="info" sx={{ mt: 2 }}>
                      Active filter: <strong>{selectedForRegeneration[0]}</strong> in dataset <strong>{config.project_id}.{config.dataset_id}</strong>
                    </Alert>
                  )}
                </Box>
              </Grid>
            </Grid>
          </Paper>
        </Grid>
      </Grid>
    </Box>
  );
};

export default GenerationPage; 