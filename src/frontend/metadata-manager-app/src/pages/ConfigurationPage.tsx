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

import React, { useEffect, useState } from 'react';
import {
  Box,
  Card,
  CardContent,
  Typography,
  TextField,
  Button,
  Alert,
  Grid,
  FormControlLabel,
  Switch,
  InputAdornment,
  IconButton,
} from '@mui/material';
import Visibility from '@mui/icons-material/Visibility';
import VisibilityOff from '@mui/icons-material/VisibilityOff';
import { DatplexConfig } from '../App';
import { getStoredApiToken, saveApiToken } from '../utils/apiClient';

interface ConfigurationPageProps {
  config: DatplexConfig;
  onConfigChange: (config: DatplexConfig) => void;
  apiToken?: string;
  onApiTokenChange?: (token: string) => void;
}

const ConfigurationPage: React.FC<ConfigurationPageProps> = ({
  config,
  onConfigChange,
  apiToken,
  onApiTokenChange,
}) => {
  const [formData, setFormData] = useState<DatplexConfig>(config);
  const [tokenInput, setTokenInput] = useState<string>(() =>
    apiToken !== undefined ? apiToken : getStoredApiToken()
  );
  const [showToken, setShowToken] = useState<boolean>(false);
  const [saveStatus, setSaveStatus] = useState<'success' | 'error' | null>(null);

  useEffect(() => {
    setFormData(config);
  }, [config]);

  useEffect(() => {
    if (apiToken !== undefined) {
      setTokenInput(apiToken);
    }
  }, [apiToken]);

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value, type, checked } = e.target;
    setFormData(prev => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : value,
    }));
  };

  const handleSave = () => {
    try {
      onConfigChange(formData);
      const normalizedToken = saveApiToken(tokenInput);
      setTokenInput(normalizedToken);
      if (onApiTokenChange) {
        onApiTokenChange(normalizedToken);
      }
      setSaveStatus('success');
      setTimeout(() => setSaveStatus(null), 3000);
    } catch (error) {
      setSaveStatus('error');
    }
  };

  const handleClearToken = () => {
    setTokenInput('');
    const cleared = saveApiToken('');
    if (onApiTokenChange) {
      onApiTokenChange(cleared);
    }
    setSaveStatus('success');
    setTimeout(() => setSaveStatus(null), 3000);
  };

  return (
    <Box>
      <Typography variant="h1" gutterBottom>
        Configuration
      </Typography>

      <Grid container spacing={3}>
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Dataplex Settings
              </Typography>

              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                <TextField
                  fullWidth
                  label="Project ID"
                  name="project_id"
                  value={formData.project_id}
                  onChange={handleInputChange}
                  helperText="The Google Cloud project ID where your Dataplex resources are located"
                />

                <TextField
                  fullWidth
                  label="LLM Location"
                  name="llm_location"
                  value={formData.llm_location}
                  onChange={handleInputChange}
                  helperText="The location of the Language Model service"
                />

                <TextField
                  fullWidth
                  label="Dataplex Location"
                  name="dataplex_location"
                  value={formData.dataplex_location}
                  onChange={handleInputChange}
                  helperText="The location of your Dataplex instance"
                />

                <FormControlLabel
                  control={
                    <Switch
                      name="stage_for_review"
                      checked={Boolean(formData.stage_for_review)}
                      onChange={handleInputChange}
                    />
                  }
                  label="Stage for Review (recommended before writing to BigQuery)"
                />

                <FormControlLabel
                  control={
                    <Switch
                      name="dry_run"
                      checked={Boolean(formData.dry_run)}
                      onChange={handleInputChange}
                    />
                  }
                  label="Dry Run (preview proposed changes without writing metadata)"
                />

                <Button
                  variant="contained"
                  onClick={handleSave}
                  sx={{ alignSelf: 'flex-start', mt: 2 }}
                >
                  Save Configuration
                </Button>

                {saveStatus === 'success' && (
                  <Alert severity="success">
                    Configuration saved successfully
                  </Alert>
                )}

                {saveStatus === 'error' && (
                  <Alert severity="error">
                    Failed to save configuration
                  </Alert>
                )}
              </Box>
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                API Authentication
              </Typography>

              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                <TextField
                  fullWidth
                  label="API Token (X-API-Key)"
                  name="api_token"
                  type={showToken ? 'text' : 'password'}
                  value={tokenInput}
                  onChange={(e) => setTokenInput(e.target.value)}
                  autoComplete="off"
                  helperText="Stored only in sessionStorage for this browser session (never localStorage) and sent in the X-API-Key header."
                  InputProps={{
                    endAdornment: (
                      <InputAdornment position="end">
                        <IconButton
                          aria-label={showToken ? 'Hide API token' : 'Show API token'}
                          onClick={() => setShowToken(prev => !prev)}
                          edge="end"
                        >
                          {showToken ? <VisibilityOff /> : <Visibility />}
                        </IconButton>
                      </InputAdornment>
                    ),
                  }}
                />

                <Box sx={{ display: 'flex', gap: 2 }}>
                  <Button
                    variant="contained"
                    onClick={handleSave}
                  >
                    Save Token
                  </Button>
                  <Button
                    variant="outlined"
                    color="secondary"
                    onClick={handleClearToken}
                    disabled={!tokenInput}
                  >
                    Clear Token
                  </Button>
                </Box>

                {tokenInput.trim() ? (
                  <Alert severity="info">
                    API token is configured for this browser session.
                  </Alert>
                ) : (
                  <Alert severity="warning">
                    No API token is configured. Authenticated backend endpoints require a valid X-API-Key token.
                  </Alert>
                )}
              </Box>
            </CardContent>
          </Card>
        </Grid>
      </Grid>
    </Box>
  );
};

export default ConfigurationPage;
