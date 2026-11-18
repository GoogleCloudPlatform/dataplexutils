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
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import axios from 'axios';
import App, { DatplexConfig } from './App';
import GenerationPage from './pages/GenerationPage';
import ConfigurationPage from './pages/ConfigurationPage';
import ReviewPage from './pages/ReviewPage';
import { CacheProvider } from './contexts/CacheContext';
import { ReviewProvider } from './contexts/ReviewContext';

jest.mock('axios', () => {
  const actual: any = {
    post: jest.fn(),
    get: jest.fn(),
    isAxiosError: (err: any) => Boolean(err && err.isAxiosError),
    defaults: { headers: { common: {} } },
  };
  return {
    __esModule: true,
    default: actual,
    ...actual,
  };
});

jest.mock('react-quill', () => {
  return function MockReactQuill(props: any) {
    return (
      <textarea
        data-testid="mock-quill"
        value={props.value || ''}
        onChange={(e) => props.onChange && props.onChange(e.target.value)}
      />
    );
  };
});

jest.mock('react-diff-viewer-continued', () => ({
  __esModule: true,
  default: (props: any) => (
    <div data-testid="mock-diff-viewer">
      <span>{props.oldValue}</span>
      <span>{props.newValue}</span>
    </div>
  ),
  DiffMethod: { WORDS: 'diffWords' },
}));

const baseConfig: DatplexConfig = {
  project_id: 'my-project',
  llm_location: 'us-central1',
  dataplex_location: 'us-central1',
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
  dataset_id: 'my_dataset',
  table_id: 'my_table',
  documentation_uri: '',
  documentation_csv_uri: '',
  strategy: 'NAIVE',
};

describe('App', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    window.sessionStorage.clear();
    window.localStorage.clear();
  });

  test('renders Metadata Manager heading and defaults stage_for_review=true, dry_run=false', () => {
    render(<App />);
    expect(screen.getByText(/Metadata Manager/i)).toBeInTheDocument();

    const stageCheckbox = screen.getByLabelText('Stage for Review') as HTMLInputElement;
    expect(stageCheckbox.checked).toBe(true);

    const dryRunCheckbox = screen.getByLabelText(
      'Dry Run (Preview Only - No Metadata Writes)'
    ) as HTMLInputElement;
    expect(dryRunCheckbox.checked).toBe(false);
  });

  test('GenerationPage sends X-API-Key, dry_run, and DOCUMENTED_THEN_REST and renders nested main.py dry-run preview', async () => {
    (axios.post as jest.Mock).mockResolvedValueOnce({
      data: {
        message: 'Dry-run completed',
        dry_run: true,
        preview: {
          dry_run: true,
          table_fqn: 'my-project.my_dataset.my_table',
          previous_description: 'Old table doc',
          proposed_description: 'New table doc',
        },
      },
    });

    const onTaskAdd = jest.fn();
    const onTaskUpdate = jest.fn();
    const onConfigChange = jest.fn();

    render(
      <GenerationPage
        config={{ ...baseConfig, dry_run: true, strategy: 'DOCUMENTED_THEN_REST' }}
        apiToken="secret-api-key"
        onTaskAdd={onTaskAdd}
        onTaskUpdate={onTaskUpdate}
        onConfigChange={onConfigChange}
      />
    );

    fireEvent.click(screen.getByText('Generate Table Description'));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledTimes(1);
    });

    const [url, payload, reqConfig] = (axios.post as jest.Mock).mock.calls[0];
    expect(url).toBe('http://localhost:8000/generate_table_description');
    expect(payload.client_options_settings.stage_for_review).toBe(true);
    expect(payload.client_options_settings.dry_run).toBe(true);
    expect(payload.dataset_settings.strategy).toBe('DOCUMENTED_THEN_REST');
    expect(reqConfig).toEqual({ headers: { 'X-API-Key': 'secret-api-key' } });

    expect(await screen.findByText(/Dry Run Preview/i)).toBeInTheDocument();
    expect(screen.getAllByText(/New table doc/).length).toBeGreaterThanOrEqual(1);
  });

  test('ConfigurationPage stores API token in sessionStorage only (not localStorage)', () => {
    const onConfigChange = jest.fn();
    const onApiTokenChange = jest.fn();

    render(
      <ConfigurationPage
        config={baseConfig}
        onConfigChange={onConfigChange}
        apiToken=""
        onApiTokenChange={onApiTokenChange}
      />
    );

    const tokenInput = screen.getByLabelText(/API Token \(X-API-Key\)/i) as HTMLInputElement;
    expect(tokenInput.type).toBe('password');

    fireEvent.change(tokenInput, { target: { value: 'session-only-token' } });
    fireEvent.click(screen.getByText('Save Configuration'));

    expect(window.sessionStorage.getItem('metadata_wizard_api_token')).toBe('session-only-token');
    expect(window.localStorage.getItem('metadata_wizard_api_token')).toBeNull();
    expect(onApiTokenChange).toHaveBeenCalledWith('session-only-token');
  });

  test('ReviewPage sends X-API-Key and sanitizes stored XSS in isHtml descriptions', async () => {
    (axios.post as jest.Mock)
      .mockResolvedValueOnce({
        data: {
          items: [
            {
              id: 'my-project.my_dataset.my_table#table',
              type: 'table',
              name: 'my-project.my_dataset.my_table',
              status: 'draft',
              lastModified: '2025-01-01T00:00:00Z',
            },
          ],
          nextPageToken: null,
          totalCount: 1,
        },
      })
      .mockResolvedValueOnce({
        data: {
          id: 'my-project.my_dataset.my_table#table',
          type: 'table',
          name: 'my-project.my_dataset.my_table',
          status: 'draft',
          isHtml: true,
          currentDescription: '<p>Safe current</p><script>window.__xss=1</script>',
          draftDescription:
            '<p onclick="alert(1)">Safe draft</p><img src=x onerror="alert(1)"><a href="javascript:alert(1)">BadLink</a><a href="https://example.com">GoodLink</a>',
          comments: [],
          tags: {},
          columns: [],
        },
      });

    render(
      <CacheProvider>
        <ReviewProvider>
          <ReviewPage config={baseConfig} apiToken="review-secret" />
        </ReviewProvider>
      </CacheProvider>
    );

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        'http://localhost:8000/metadata/review',
        expect.any(Object),
        { headers: { 'X-API-Key': 'review-secret' } }
      );
    });

    const tableRowCell = await screen.findByText('my-project.my_dataset.my_table');
    fireEvent.click(tableRowCell);

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        'http://localhost:8000/metadata/review/details',
        expect.any(Object),
        { headers: { 'X-API-Key': 'review-secret' } }
      );
    });

    // Switch from diff view to side-by-side HTML view
    const diffSwitch = await screen.findByLabelText(/Showing Diff View/i);
    fireEvent.click(diffSwitch);

    expect(await screen.findByText('Safe current')).toBeInTheDocument();
    expect(screen.getByText('Safe draft')).toBeInTheDocument();
    expect(document.querySelector('script')).toBeNull();
    expect(document.querySelector('img')).toBeNull();

    const goodLink = screen.getByText('GoodLink') as HTMLAnchorElement;
    expect(goodLink.getAttribute('href')).toBe('https://example.com/');
    expect(goodLink.getAttribute('rel')).toBe('noopener noreferrer');

    const badLinkText = screen.getByText('BadLink');
    expect(badLinkText.tagName.toLowerCase()).not.toBe('a');
  });

  test('ReviewPage safely handles missing /update_column_draft_description for column edits', async () => {
    (axios.post as jest.Mock)
      .mockResolvedValueOnce({
        data: {
          items: [
            {
              id: 'my-project.my_dataset.my_table#table',
              type: 'table',
              name: 'my-project.my_dataset.my_table',
              status: 'draft',
              lastModified: '2025-01-01T00:00:00Z',
            },
          ],
          nextPageToken: null,
          totalCount: 1,
        },
      })
      .mockResolvedValueOnce({
        data: {
          id: 'my-project.my_dataset.my_table#table',
          type: 'table',
          name: 'my-project.my_dataset.my_table',
          status: 'draft',
          isHtml: false,
          currentDescription: 'Table current',
          draftDescription: 'Table draft',
          comments: [],
          tags: {},
          columns: [
            {
              id: 'my-project.my_dataset.my_table#table#column.col1',
              name: 'my-project.my_dataset.my_table.col1',
              type: 'column',
              currentDescription: 'Col current',
              draftDescription: 'Col draft',
              isHtml: false,
              status: 'draft',
              metadata: {},
            },
          ],
        },
      })
      .mockResolvedValueOnce({
        data: {
          id: 'my-project.my_dataset.my_table#table',
          type: 'table',
          name: 'my-project.my_dataset.my_table',
          status: 'draft',
          isHtml: false,
          currentDescription: 'Table current',
          draftDescription: 'Table draft',
          comments: [],
          tags: {},
          columns: [
            {
              id: 'my-project.my_dataset.my_table#table#column.col1',
              name: 'my-project.my_dataset.my_table.col1',
              type: 'column',
              currentDescription: 'Col current',
              draftDescription: 'Col draft',
              isHtml: false,
              status: 'draft',
              metadata: {},
            },
          ],
        },
      });

    render(
      <CacheProvider>
        <ReviewProvider>
          <ReviewPage config={baseConfig} apiToken="review-secret" />
        </ReviewProvider>
      </CacheProvider>
    );

    const tableRowCell = await screen.findByText('my-project.my_dataset.my_table');
    fireEvent.click(tableRowCell);

    const nextColButton = await screen.findByRole('button', { name: /Next Column/i });
    await waitFor(() => expect(nextColButton).not.toBeDisabled());
    fireEvent.click(nextColButton);

    const editButton = await screen.findByRole('button', { name: /^Edit$/i });
    fireEvent.click(editButton);

    expect(
      await screen.findByText(/does not implement \/update_column_draft_description/i)
    ).toBeInTheDocument();
    const saveButton = screen.getByRole('button', { name: /^Save$/i }) as HTMLButtonElement;
    expect(saveButton.disabled).toBe(true);

    const calledUrls = (axios.post as jest.Mock).mock.calls.map((c) => c[0]);
    expect(calledUrls.some((u: string) => u.includes('update_column_draft_description'))).toBe(false);
  });

  test('ReviewPage sanitizes HTML passed into ReactQuill Edit Dialog and syncs/resets isRichText', async () => {
    (axios.post as jest.Mock)
      .mockResolvedValueOnce({
        data: {
          items: [
            {
              id: 'my-project.my_dataset.my_table#table',
              type: 'table',
              name: 'my-project.my_dataset.my_table',
              status: 'draft',
              lastModified: '2025-01-01T00:00:00Z',
            },
            {
              id: 'my-project.my_dataset.plain_table#table',
              type: 'table',
              name: 'my-project.my_dataset.plain_table',
              status: 'draft',
              lastModified: '2025-01-01T00:00:00Z',
            },
          ],
          nextPageToken: null,
          totalCount: 2,
        },
      })
      .mockResolvedValueOnce({
        data: {
          id: 'my-project.my_dataset.my_table#table',
          type: 'table',
          name: 'my-project.my_dataset.my_table',
          status: 'draft',
          isHtml: true,
          currentDescription: '<p>Safe current</p>',
          draftDescription:
            '<p>Safe edit</p><img src=x onerror="alert(1)"><script>alert(2)</script><a href="javascript:alert(3)">BadLink</a><a href="https://example.com">GoodLink</a>',
          comments: [],
          tags: {},
          columns: [],
        },
      })
      .mockResolvedValueOnce({
        data: {
          id: 'my-project.my_dataset.plain_table#table',
          type: 'table',
          name: 'my-project.my_dataset.plain_table',
          status: 'draft',
          isHtml: false,
          currentDescription: 'Plain current',
          draftDescription: 'Plain <img src=x onerror=alert(1)> text',
          comments: [],
          tags: {},
          columns: [],
        },
      });

    render(
      <CacheProvider>
        <ReviewProvider>
          <ReviewPage config={baseConfig} apiToken="review-secret" />
        </ReviewProvider>
      </CacheProvider>
    );

    const tableRowCell = await screen.findByText('my-project.my_dataset.my_table');
    fireEvent.click(tableRowCell);

    const editButton = await screen.findByRole('button', { name: /^Edit$/i });
    await waitFor(() => expect(editButton).toBeInTheDocument());
    fireEvent.click(editButton);

    const quillEditor = (await screen.findByTestId('mock-quill')) as HTMLTextAreaElement;
    expect(quillEditor.value).toContain('<p>Safe edit</p>');
    expect(quillEditor.value).toContain('<a href="https://example.com/" rel="noopener noreferrer">GoodLink</a>');
    expect(quillEditor.value).toContain('BadLink');
    expect(quillEditor.value).not.toContain('<img');
    expect(quillEditor.value).not.toContain('onerror');
    expect(quillEditor.value).not.toContain('<script');
    expect(quillEditor.value).not.toContain('javascript:');

    // Close dialog, navigate to plain-text item, and verify rich-text mode resets to plain text (no ReactQuill)
    fireEvent.click(screen.getByRole('button', { name: /^Cancel$/i }));
    await waitFor(
      () => expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
      { timeout: 3000 }
    );
    const nextButton = await screen.findByRole('button', { name: /^Next$/i }, { timeout: 3000 });
    fireEvent.click(nextButton);

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledTimes(3);
    });
    await waitFor(() => {
      expect(screen.getByText('my-project.my_dataset.plain_table')).toBeInTheDocument();
    });

    const nextEditButton = await screen.findByRole('button', { name: /^Edit$/i });
    fireEvent.click(nextEditButton);

    const plainField = (await screen.findByLabelText(/Draft Description/i)) as HTMLInputElement;
    expect(plainField.value).toBe('Plain <img src=x onerror=alert(1)> text');
    expect(screen.queryByTestId('mock-quill')).toBeNull();
  });
});
