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

import { escapeHtmlText, isSafeHttpUrl, sanitizeHtml, stripHtmlToText } from './sanitizeHtml';

describe('sanitizeHtml', () => {
  test('preserves safe formatting tags', () => {
    const input =
      '<h1>Title</h1><p>Hello <b>bold</b> <strong>strong</strong> <i>italic</i> <em>em</em> <u>u</u> <s>s</s></p>' +
      '<ul><li>one</li><li>two</li></ul><ol><li>first</li></ol>' +
      '<pre><code>SELECT 1</code></pre><blockquote>quote</blockquote><br>';
    const output = sanitizeHtml(input);
    expect(output).toContain('<h1>Title</h1>');
    expect(output).toContain('<strong>strong</strong>');
    expect(output).toContain('<ul><li>one</li><li>two</li></ul>');
    expect(output).toContain('<pre><code>SELECT 1</code></pre>');
    expect(output).toContain('<blockquote>quote</blockquote>');
    expect(output).toContain('<br>');
  });

  test('allows http/https links with rel="noopener noreferrer" and strips other attributes', () => {
    const input =
      '<a href="https://example.com/docs?a=1" onclick="alert(1)" style="color:red" target="_blank">Docs</a>' +
      '<a href="http://example.org">HTTP</a>';
    const output = sanitizeHtml(input);
    expect(output).toContain('<a href="https://example.com/docs?a=1" rel="noopener noreferrer">Docs</a>');
    expect(output).toContain('<a href="http://example.org/" rel="noopener noreferrer">HTTP</a>');
    expect(output).not.toContain('onclick');
    expect(output).not.toContain('style=');
    expect(output).not.toContain('target=');
  });

  test('strips dangerous tags and their payloads completely', () => {
    const input =
      '<p>Safe</p>' +
      '<script>alert("xss")</script>' +
      '<iframe src="https://evil.example">fallback</iframe>' +
      '<object data="evil.swf">obj</object>' +
      '<embed src="evil.swf">' +
      '<svg onload="alert(1)"><circle /></svg>' +
      '<style>body { background: url("javascript:alert(1)"); }</style>';
    const output = sanitizeHtml(input);
    expect(output).toBe('<p>Safe</p>');
    expect(output).not.toContain('script');
    expect(output).not.toContain('iframe');
    expect(output).not.toContain('object');
    expect(output).not.toContain('embed');
    expect(output).not.toContain('svg');
    expect(output).not.toContain('style');
    expect(output).not.toContain('alert');
  });

  test('strips event handler attributes and unsafe URLs', () => {
    const input =
      '<p onclick="alert(1)" onmouseover="evil()" style="position:fixed">Text</p>' +
      '<img src="x" onerror="alert(1)">' +
      '<a href="javascript:alert(1)">Bad JS</a>' +
      '<a href="data:text/html,<script>alert(1)</script>">Bad Data</a>' +
      '<a href="java\tscript:alert(1)">Tab JS</a>';
    const output = sanitizeHtml(input);
    expect(output).toContain('<p>Text</p>');
    expect(output).not.toContain('onclick');
    expect(output).not.toContain('onmouseover');
    expect(output).not.toContain('onerror');
    expect(output).not.toContain('style=');
    expect(output).not.toContain('javascript:');
    expect(output).not.toContain('data:');
    expect(output).not.toContain('<img');
    expect(output).toContain('Bad JS');
    expect(output).toContain('Bad Data');
  });

  test('unwraps unknown containers without re-parsing text as HTML', () => {
    const output = sanitizeHtml('<div>&lt;script&gt;alert(1)&lt;/script&gt;</div>');
    expect(output).toBe('&lt;script&gt;alert(1)&lt;/script&gt;');
  });

  test('isSafeHttpUrl rejects non-http schemes and control chars', () => {
    expect(isSafeHttpUrl('https://cloud.google.com/dataplex')).toBe('https://cloud.google.com/dataplex');
    expect(isSafeHttpUrl('http://localhost:8000')).toBe('http://localhost:8000/');
    expect(isSafeHttpUrl('javascript:alert(1)')).toBeNull();
    expect(isSafeHttpUrl('data:text/html,hi')).toBeNull();
    expect(isSafeHttpUrl('https://example.com/\nalert')).toBeNull();
    expect(isSafeHttpUrl('/relative/path')).toBeNull();
  });

  test('strips form elements and resists DOM clobbering', () => {
    const input =
      '<p>Before</p><form id="body"><input name="nodeType" value="3"><input name="childNodes"><img src=x onerror="alert(1)"></form><p>After</p>';
    const output = sanitizeHtml(input);
    expect(output).toBe('<p>Before</p><p>After</p>');
    expect(output).not.toContain('form');
    expect(output).not.toContain('input');
    expect(output).not.toContain('onerror');
  });

  test('stripHtmlToText and escapeHtmlText handle payloads safely', () => {
    expect(stripHtmlToText('<p>Hello <b>world</b></p>')).toBe('Hello world');
    expect(escapeHtmlText('<img src=x onerror=alert(1)>')).toBe(
      '&lt;img src=x onerror=alert(1)&gt;'
    );
  });
});
