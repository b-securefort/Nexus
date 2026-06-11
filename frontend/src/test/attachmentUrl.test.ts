import { describe, it, expect } from 'vitest';
import {
  drawioSiblingUrl,
  isAllowedAttachmentUrl,
  resolveAttachmentUrl,
} from '../components/MessageBubble';

// VITE_API_BASE_URL in tests defaults to http://localhost:8000 (see
// frontend/.env). These tests confirm that:
//   - relative paths and blob: URLs are accepted
//   - absolute URLs only resolve when their origin matches the API base
//   - arbitrary external origins are rejected (would otherwise be a tracker
//     / SVG-XSS vector if an assistant tool emitted them)
describe('isAllowedAttachmentUrl', () => {
  it('accepts relative API paths', () => {
    expect(isAllowedAttachmentUrl('/api/uploads/abc.png')).toBe(true);
    expect(isAllowedAttachmentUrl('/api/output/foo.svg')).toBe(true);
  });

  it('accepts blob: URLs for local previews', () => {
    expect(isAllowedAttachmentUrl('blob:http://localhost:5173/123')).toBe(true);
  });

  it('accepts absolute URLs matching the API origin', () => {
    expect(isAllowedAttachmentUrl('http://localhost:8000/api/uploads/x.png')).toBe(true);
  });

  it('rejects arbitrary external HTTP origins', () => {
    expect(isAllowedAttachmentUrl('https://attacker.example/img.png')).toBe(false);
    expect(isAllowedAttachmentUrl('http://evil.test/track.gif')).toBe(false);
  });

  it('rejects data: URLs (SVG-via-data: XSS vector)', () => {
    expect(isAllowedAttachmentUrl('data:image/svg+xml;base64,PHN2Zy8+')).toBe(false);
    expect(isAllowedAttachmentUrl('data:text/html,<script>alert(1)</script>')).toBe(false);
  });

  it('rejects javascript: URLs', () => {
    expect(isAllowedAttachmentUrl('javascript:alert(1)')).toBe(false);
  });

  it('rejects empty and malformed input', () => {
    expect(isAllowedAttachmentUrl('')).toBe(false);
    expect(isAllowedAttachmentUrl('not a url')).toBe(false);
  });
});

describe('resolveAttachmentUrl', () => {
  it('returns null for disallowed URLs', () => {
    expect(resolveAttachmentUrl('https://attacker.example/img.png')).toBeNull();
    expect(resolveAttachmentUrl('data:image/png;base64,xxx')).toBeNull();
  });

  it('prepends API base for relative paths', () => {
    expect(resolveAttachmentUrl('/api/uploads/x.png')).toBe(
      'http://localhost:8000/api/uploads/x.png',
    );
  });

  it('passes blob: URLs through unchanged', () => {
    const blob = 'blob:http://localhost:5173/abc-123';
    expect(resolveAttachmentUrl(blob)).toBe(blob);
  });

  it('passes allowed absolute URLs through unchanged', () => {
    const url = 'http://localhost:8000/api/output/diagram.png';
    expect(resolveAttachmentUrl(url)).toBe(url);
  });
});

// The download affordance on a rendered diagram offers the editable .drawio
// source next to the PNG. Only output-sandbox renders qualify.
describe('drawioSiblingUrl', () => {
  it('maps an output render to its .drawio sibling', () => {
    expect(drawioSiblingUrl('/api/output/diagram.png')).toBe('/api/output/diagram.drawio');
    expect(drawioSiblingUrl('http://localhost:8000/api/output/d.svg')).toBe(
      'http://localhost:8000/api/output/d.drawio',
    );
  });

  it('drops cache-bust query strings', () => {
    expect(drawioSiblingUrl('/api/output/d.png?v=abc')).toBe('/api/output/d.drawio');
  });

  it('returns null for uploads, previews, and external URLs', () => {
    expect(drawioSiblingUrl('/api/uploads/photo.png')).toBeNull();
    expect(drawioSiblingUrl('blob:http://localhost:5173/123')).toBeNull();
    expect(drawioSiblingUrl('https://attacker.example/api/output/x.png')).toBeNull();
    expect(drawioSiblingUrl('')).toBeNull();
  });

  it('returns null for non-image output files', () => {
    expect(drawioSiblingUrl('/api/output/script.ps1')).toBeNull();
    expect(drawioSiblingUrl('/api/output/already.drawio')).toBeNull();
  });
});
