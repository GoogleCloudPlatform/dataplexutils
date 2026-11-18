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

import axios from 'axios';
import {
  configureAxiosAuth,
  getAuthHeaders,
  getStoredApiToken,
  saveApiToken,
} from './apiClient';

jest.mock('axios', () => {
  const actual: any = {
    isAxiosError: (err: any) => Boolean(err && err.isAxiosError),
    defaults: { headers: { common: {} } },
  };
  return {
    __esModule: true,
    default: actual,
    ...actual,
  };
});

describe('apiClient auth token handling', () => {
  const originalEnv = process.env;

  beforeEach(() => {
    window.sessionStorage.clear();
    window.localStorage.clear();
    if (axios.defaults?.headers?.common) {
      delete axios.defaults.headers.common['X-API-Key'];
    }
    process.env = { ...originalEnv };
    delete process.env.REACT_APP_API_TOKEN;
  });

  afterAll(() => {
    process.env = originalEnv;
  });

  test('returns empty string by default with no hardcoded fallback secret', () => {
    expect(getStoredApiToken()).toBe('');
    expect(getAuthHeaders()).toEqual({});
  });

  test('uses REACT_APP_API_TOKEN only when explicitly set for local dev', () => {
    process.env.REACT_APP_API_TOKEN = 'dev-env-token';
    expect(getStoredApiToken()).toBe('dev-env-token');
    expect(getAuthHeaders()).toEqual({ 'X-API-Key': 'dev-env-token' });
  });

  test('stores token in sessionStorage (never localStorage) and sets X-API-Key', () => {
    const saved = saveApiToken('  my-secret-token  ');
    expect(saved).toBe('my-secret-token');
    expect(window.sessionStorage.getItem('metadata_wizard_api_token')).toBe('my-secret-token');
    expect(window.localStorage.length).toBe(0);
    expect(getAuthHeaders()).toEqual({ 'X-API-Key': 'my-secret-token' });
    expect(axios.defaults.headers.common['X-API-Key']).toBe('my-secret-token');
  });

  test('clearing token removes X-API-Key and overrides env fallback for session', () => {
    process.env.REACT_APP_API_TOKEN = 'dev-env-token';
    saveApiToken('custom-token');
    saveApiToken('');
    expect(getStoredApiToken()).toBe('');
    expect(getAuthHeaders()).toEqual({});
    expect(axios.defaults.headers.common['X-API-Key']).toBeUndefined();
  });

  test('configureAxiosAuth updates defaults safely', () => {
    configureAxiosAuth('abc123');
    expect(axios.defaults.headers.common['X-API-Key']).toBe('abc123');
    configureAxiosAuth('');
    expect(axios.defaults.headers.common['X-API-Key']).toBeUndefined();
  });
});
