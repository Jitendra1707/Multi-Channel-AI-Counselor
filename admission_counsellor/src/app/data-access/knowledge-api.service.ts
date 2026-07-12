import { Injectable } from '@angular/core';
import { environment } from '../../environments/environment';

/**
 * KnowledgeApiService — client for AegisBackend's RAG resource endpoints (:8001
 * /api/resources). Plain fetch, mirroring the proven web-app client
 * (web-app/src/lib/api.ts). The FE only uploads/lists/deletes; the backend owns
 * extraction, chunking, embedding and indexing — the AI counselor then retrieves
 * from the resulting index automatically.
 */

const BASE = environment.aegisUrl.replace(/\/$/, '');

/** One ingested knowledge document, as returned by the backend. */
export interface ResourceDoc {
  id: string;
  filename: string;
  size?: number;
  chunks?: number;
  status?: 'pending' | 'processing' | 'ready' | 'failed';
}

@Injectable({ providedIn: 'root' })
export class KnowledgeApiService {
  /**
   * Upload one document (pdf / txt / md) as RAG context. multipart/form-data —
   * we do NOT set Content-Type so the browser adds the correct boundary.
   * Throws 'UPLOAD_UNAVAILABLE' when the backend is unreachable or the ingestion
   * endpoint isn't mounted (404), so the UI can show a friendly message.
   */
  async uploadResource(file: File): Promise<ResourceDoc> {
    const url = `${BASE}/api/resources`;
    const form = new FormData();
    form.append('file', file);
    console.info('[KnowledgeApi] POST', url, '→ uploading', file.name, `(${file.size} bytes)`);
    let res: Response;
    try {
      res = await fetch(url, { method: 'POST', body: form });
    } catch (e) {
      console.error('[KnowledgeApi] POST failed (backend unreachable)', url, e);
      throw new Error('UPLOAD_UNAVAILABLE');
    }
    if (!res.ok) {
      if (res.status === 404) {
        console.error('[KnowledgeApi] POST 404 — ingestion endpoint not mounted', url);
        throw new Error('UPLOAD_UNAVAILABLE');
      }
      let detail = `HTTP ${res.status}`;
      try { detail = (await res.json())?.detail ?? detail; } catch { /* ignore */ }
      console.error('[KnowledgeApi] POST error', res.status, detail);
      throw new Error(detail);
    }
    const doc = (await res.json()) as ResourceDoc;
    console.info('[KnowledgeApi] POST ok — ingested', doc);
    return doc;
  }

  /** List uploaded resources. Returns [] if the backend endpoint isn't up yet. */
  async listResources(): Promise<ResourceDoc[]> {
    const url = `${BASE}/api/resources`;
    console.info('[KnowledgeApi] GET', url, '→ fetching collection documents');
    try {
      const res = await fetch(url);
      console.info('[KnowledgeApi] GET status', res.status, res.ok ? 'OK' : 'NOT OK');
      if (!res.ok) {
        console.warn('[KnowledgeApi] GET non-2xx — returning [] (mock library stays)');
        return [];
      }
      const docs = (await res.json()) as ResourceDoc[];
      console.info(`[KnowledgeApi] GET ok — ${docs.length} document(s) from backend:`, docs);
      return docs;
    } catch (e) {
      console.error('[KnowledgeApi] GET failed (backend unreachable) — returning []', url, e);
      return [];
    }
  }

  /** Remove an uploaded resource (and its chunks) by id. */
  async deleteResource(id: string): Promise<void> {
    const url = `${BASE}/api/resources/${encodeURIComponent(id)}`;
    console.info('[KnowledgeApi] DELETE', url);
    let res: Response;
    try {
      res = await fetch(url, { method: 'DELETE' });
    } catch (e) {
      console.error('[KnowledgeApi] DELETE failed (backend unreachable)', url, e);
      throw new Error('UPLOAD_UNAVAILABLE');
    }
    console.info('[KnowledgeApi] DELETE status', res.status);
    if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`);
  }
}
