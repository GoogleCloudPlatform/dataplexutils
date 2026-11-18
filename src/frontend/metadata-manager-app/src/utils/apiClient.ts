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

const API_TOKEN_SESSION_KEY = 'metadata_wizard_api_token';
const DEFAULT_API_BASE_URL = 'http://localhost:8000';

/**
 * Return the normalized backend base URL without trailing slashes.
 */
export function getApiBaseUrl(): string {
  const configured =
    typeof process !== 'undefined' && process.env && process.env.REACT_APP_API_URL
      ? process.env.REACT_APP_API_URL
      : DEFAULT_API_BASE_URL;
  return configured.replace(/\/+$/, '');
}

/**
 * Retrieve the API token from sessionStorage (never localStorage),
 * falling back to REACT_APP_API_TOKEN only when explicitly set for local dev.
 */
export function getStoredApiToken(): string {
  try {
    if (typeof window !== 'undefined' && window.sessionStorage) {
      const stored = window.sessionStorage.getItem(API_TOKEN_SESSION_KEY);
      if (stored !== null) {
        return stored;
      }
    }
  } catch {
    // Ignore storage errors in restricted browser contexts.
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

/**
 * Configure axios default headers so any request carries X-API-Key when a token is set.
 */
export function configureAxiosAuth(token: string): void {
  const trimmed = token.trim();
  const commonHeaders = axios?.defaults?.headers?.common;
  if (!commonHeaders) {
    return;
  }
  if (trimmed) {
    commonHeaders['X-API-Key'] = trimmed;
  } else {
    delete commonHeaders['X-API-Key'];
  }
}

/**
 * Persist the API token in sessionStorage (never localStorage) and sync axios defaults.
 */
export function saveApiToken(token: string): string {
  const normalized = token.trim();
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
          // Keep explicit clear for the current browser session.
          window.sessionStorage.setItem(API_TOKEN_SESSION_KEY, '');
        } else {
          window.sessionStorage.removeItem(API_TOKEN_SESSION_KEY);
        }
      }
    }
  } catch {
    // Ignore storage errors in restricted browser contexts.
  }

  configureAxiosAuth(normalized);
  return normalized;
}

/**
 * Return request headers including X-API-Key when a token is available.
 */
export function getAuthHeaders(tokenOverride?: string): Record<string, string> {
  const effectiveToken = (tokenOverride !== undefined ? tokenOverride : getStoredApiToken()).trim();
  return effectiveToken ? { 'X-API-Key': effectiveToken } : {};
}

/**
 * Build an Axios request config containing auth headers.
 */
export function getAxiosRequestConfig(tokenOverride?: string): { headers: Record<string, string> } {
  return {
    headers: getAuthHeaders(tokenOverride),
  };
}

/**
 * Extract a safe display message from an Axios/API error without crashing on objects.
 */
export function extractApiErrorMessage(error: unknown, fallback: string): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === 'string' && detail.trim()) {
      return detail;
    }
    if (detail !== undefined && detail !== null) {
      return typeof detail === 'object' ? JSON.stringify(detail) : String(detail);
    }
    if (error.message) {
      return error.message;
    }
  } else if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
}
