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

const ALLOWED_TAGS = new Set([
  'p',
  'br',
  'b',
  'strong',
  'i',
  'em',
  'u',
  's',
  'ul',
  'ol',
  'li',
  'code',
  'pre',
  'blockquote',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'a',
]);

// Elements whose subtrees contain executable code, styles, or active embedded content.
const DROP_CONTENT_TAGS = new Set([
  'script',
  'style',
  'iframe',
  'object',
  'embed',
  'svg',
  'math',
  'noscript',
  'template',
  'textarea',
  'title',
  'xmp',
  'noembed',
  'noframes',
  'frame',
  'frameset',
  'applet',
  'audio',
  'video',
  'source',
  'canvas',
  'form',
]);

const nodeTypeGetter =
  typeof Node !== 'undefined'
    ? Object.getOwnPropertyDescriptor(Node.prototype, 'nodeType')?.get
    : undefined;
const childNodesGetter =
  typeof Node !== 'undefined'
    ? Object.getOwnPropertyDescriptor(Node.prototype, 'childNodes')?.get
    : undefined;
const nodeValueGetter =
  typeof Node !== 'undefined'
    ? Object.getOwnPropertyDescriptor(Node.prototype, 'nodeValue')?.get
    : undefined;
const tagNameGetter =
  typeof Element !== 'undefined'
    ? Object.getOwnPropertyDescriptor(Element.prototype, 'tagName')?.get
    : undefined;

function getNodeType(node: Node): number {
  return nodeTypeGetter ? nodeTypeGetter.call(node) : node.nodeType;
}

function getChildNodes(node: Node): NodeListOf<ChildNode> {
  return childNodesGetter ? childNodesGetter.call(node) : node.childNodes;
}

function getNodeValue(node: Node): string {
  return (nodeValueGetter ? nodeValueGetter.call(node) : node.nodeValue) || '';
}

function getTagName(element: Element): string {
  return String((tagNameGetter ? tagNameGetter.call(element) : element.tagName) || '').toLowerCase();
}

// ASCII control characters (including CR/LF/TAB) disallowed inside href URLs.
// eslint-disable-next-line no-control-regex
const CONTROL_CHARS_RE = /[\u0000-\u001f\u007f]/;

/**
 * Escape raw text for safe inclusion in HTML text contexts.
 */
export function escapeHtmlText(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/**
 * Validate that an anchor href uses strictly http:// or https:// without control characters.
 */
export function isSafeHttpUrl(rawUrl: string): string | null {
  if (typeof rawUrl !== 'string') {
    return null;
  }
  const trimmed = rawUrl.trim();
  if (!trimmed || CONTROL_CHARS_RE.test(trimmed)) {
    return null;
  }
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      return null;
    }
    return parsed.href;
  } catch {
    return null;
  }
}

/**
 * Extract plain text from HTML using DOMParser (avoids live DOM innerHTML execution).
 */
export function stripHtmlToText(html: string): string {
  if (!html || typeof html !== 'string') {
    return '';
  }
  if (typeof DOMParser === 'undefined') {
    return html.replace(/<[^>]*>/g, '');
  }
  const doc = new DOMParser().parseFromString(html, 'text/html');
  return doc.body?.textContent || '';
}

function appendSanitizedChildren(
  sourceParent: Node,
  targetParent: Node,
  outDoc: Document
): void {
  const children = getChildNodes(sourceParent);
  for (let i = 0; i < children.length; i += 1) {
    const child = children[i];
    const childType = getNodeType(child);
    if (childType === 3 /* Node.TEXT_NODE */) {
      targetParent.appendChild(outDoc.createTextNode(getNodeValue(child)));
      continue;
    }

    if (childType !== 1 /* Node.ELEMENT_NODE */) {
      // Drop comments, processing instructions, etc.
      continue;
    }

    const element = child as Element;
    const tagName = getTagName(element);

    if (DROP_CONTENT_TAGS.has(tagName)) {
      continue;
    }

    if (!ALLOWED_TAGS.has(tagName)) {
      // Unwrap unknown non-dangerous containers while sanitizing their descendants.
      appendSanitizedChildren(element, targetParent, outDoc);
      continue;
    }

    if (tagName === 'a') {
      const rawHref = Element.prototype.getAttribute.call(element, 'href');
      const safeHref = rawHref ? isSafeHttpUrl(rawHref) : null;
      if (!safeHref) {
        // Strip unsafe/missing links down to their sanitized text/descendants.
        appendSanitizedChildren(element, targetParent, outDoc);
        continue;
      }
      const anchor = outDoc.createElement('a');
      anchor.setAttribute('href', safeHref);
      anchor.setAttribute('rel', 'noopener noreferrer');
      appendSanitizedChildren(element, anchor, outDoc);
      targetParent.appendChild(anchor);
      continue;
    }

    const cleanElement = outDoc.createElement(tagName);
    if (tagName !== 'br') {
      appendSanitizedChildren(element, cleanElement, outDoc);
    }
    targetParent.appendChild(cleanElement);
  }
}

/**
 * Strictly sanitize untrusted HTML against an allowlist of safe formatting tags.
 * Strips scripts, active containers, event handlers, inline styles, and unsafe URLs.
 */
export function sanitizeHtml(dirtyHtml: string): string {
  if (!dirtyHtml || typeof dirtyHtml !== 'string') {
    return '';
  }

  if (
    typeof DOMParser === 'undefined' ||
    typeof document === 'undefined' ||
    !document.implementation?.createHTMLDocument
  ) {
    return escapeHtmlText(dirtyHtml);
  }

  const parsedDoc = new DOMParser().parseFromString(dirtyHtml, 'text/html');
  const outDoc = document.implementation.createHTMLDocument('');
  const container = outDoc.createElement('div');

  if (parsedDoc.body) {
    appendSanitizedChildren(parsedDoc.body, container, outDoc);
  }

  return container.innerHTML;
}
