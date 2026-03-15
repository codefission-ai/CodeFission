/**
 * Shared file attachment utilities — drag-drop reading, upload, and the
 * useFileAttach hook used by every message input in the app.
 *
 * When treeId/parentNodeId are provided, files are uploaded eagerly to a
 * draft child workspace (created on first attach) instead of being staged
 * in memory.  This ensures files end up in the child workspace, never the
 * parent's.
 */

import { useState, useRef, useCallback, useEffect } from "react";

// ── Types ──────────────────────────────────────────────────────────────

export interface DroppedFile {
  path: string;
  file: File;
}

// ── Constants ──────────────────────────────────────────────────────────

export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

// ── Drag-drop helpers ──────────────────────────────────────────────────

function readEntry(entry: FileSystemEntry, basePath: string): Promise<DroppedFile[]> {
  return new Promise((resolve) => {
    if (entry.isFile) {
      (entry as FileSystemFileEntry).file(
        (f) => resolve([{ path: basePath + f.name, file: f }]),
        () => resolve([]),
      );
    } else if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      const results: DroppedFile[] = [];
      const readBatch = () => {
        reader.readEntries(async (entries) => {
          if (entries.length === 0) { resolve(results); return; }
          for (const e of entries) {
            const sub = await readEntry(e, basePath + entry.name + "/");
            results.push(...sub);
          }
          readBatch();
        }, () => resolve(results));
      };
      readBatch();
    } else {
      resolve([]);
    }
  });
}

export interface DropResult {
  files: DroppedFile[];
  label: string;
}

/** Collect all files from a drop event (supports folders via webkitGetAsEntry).
 *  Single folder drop: strip folder prefix so contents become workspace root. */
export async function collectDroppedFiles(e: React.DragEvent): Promise<DropResult> {
  const items = e.dataTransfer?.items;
  if (!items) return { files: [], label: "" };

  const topEntries: FileSystemEntry[] = [];
  const looseFallback: DroppedFile[] = [];
  for (let i = 0; i < items.length; i++) {
    const entry = items[i].webkitGetAsEntry?.();
    if (entry) {
      topEntries.push(entry);
    } else {
      const f = items[i].getAsFile();
      if (f) looseFallback.push({ path: f.name, file: f });
    }
  }

  const nested = await Promise.all(topEntries.map((ent) => readEntry(ent, "")));
  const all = looseFallback.concat(...nested);

  // Single directory dropped → strip its name prefix so contents become workspace root
  const isSingleDir = topEntries.length === 1 && topEntries[0].isDirectory && looseFallback.length === 0;
  if (isSingleDir) {
    const prefix = topEntries[0].name + "/";
    for (const f of all) {
      if (f.path.startsWith(prefix)) f.path = f.path.slice(prefix.length);
    }
    return { files: all, label: topEntries[0].name };
  }

  const dirs = topEntries.filter((ent) => ent.isDirectory).length;
  const fileCount = topEntries.length - dirs + looseFallback.length;
  const parts: string[] = [];
  if (dirs > 0) parts.push(`${dirs} folder${dirs > 1 ? "s" : ""}`);
  if (fileCount > 0) parts.push(`${fileCount} file${fileCount > 1 ? "s" : ""}`);
  return { files: all, label: parts.join(", ") };
}

// ── Upload ─────────────────────────────────────────────────────────────

export async function uploadFiles(
  treeId: string, nodeId: string, files: DroppedFile[],
): Promise<{ count: number; git_commit: string } | null> {
  const totalSize = files.reduce((sum, f) => sum + f.file.size, 0);
  if (totalSize > MAX_UPLOAD_BYTES) {
    alert(`Upload too large (${(totalSize / 1024 / 1024).toFixed(1)}MB). Max is 50MB.`);
    return null;
  }
  const form = new FormData();
  for (const f of files) {
    form.append("files", f.file);
    form.append("paths", f.path);
  }
  const resp = await fetch(`/api/trees/${treeId}/nodes/${nodeId}/upload`, {
    method: "POST",
    body: form,
  });
  if (!resp.ok) {
    console.error("Upload failed:", await resp.text());
    return null;
  }
  return resp.json();
}

// ── Draft helpers ─────────────────────────────────────────────────────

async function prepareDraft(
  treeId: string, parentNodeId: string,
): Promise<string | null> {
  const resp = await fetch(
    `/api/trees/${treeId}/nodes/${parentNodeId}/prepare-draft`,
    { method: "POST" },
  );
  if (!resp.ok) return null;
  const data = await resp.json();
  return data.draft_node_id;
}

async function discardDraftApi(treeId: string, draftId: string): Promise<void> {
  await fetch(`/api/trees/${treeId}/drafts/${draftId}`, { method: "DELETE" });
}

// ── Format helpers ─────────────────────────────────────────────────────

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

// ── Hook ───────────────────────────────────────────────────────────────

export interface FileAttachOpts {
  /** Tree ID — required for eager draft upload. */
  treeId?: string;
  /** Parent node ID — files are uploaded to a draft child of this node. */
  parentNodeId?: string;
}

export function useFileAttach(opts?: FileAttachOpts) {
  const [pendingFiles, setPendingFiles] = useState<DroppedFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Draft tracking
  const [draftNodeId, setDraftNodeId] = useState<string | null>(null);
  const [uploadedQuotes, setUploadedQuotes] = useState<
    Array<{ node_id: string; type: string; path: string }>
  >([]);
  // Refs to avoid stale closures
  const draftRef = useRef<string | null>(null);
  draftRef.current = draftNodeId;
  const optsRef = useRef(opts);
  optsRef.current = opts;

  // Whether we can do eager draft uploads
  const canEagerUpload = !!(opts?.treeId && opts?.parentNodeId);

  // Discard draft when parent changes or on unmount
  useEffect(() => {
    return () => {
      if (draftRef.current && optsRef.current?.treeId) {
        discardDraftApi(optsRef.current.treeId, draftRef.current).catch(() => {});
      }
    };
  }, [opts?.parentNodeId]);

  // Reset draft state when parent changes
  useEffect(() => {
    setDraftNodeId(null);
    setUploadedQuotes([]);
    setPendingFiles([]);
  }, [opts?.parentNodeId]);

  /** Ensure a draft child exists, create one if needed. */
  const ensureDraft = useCallback(async (): Promise<string | null> => {
    if (draftRef.current) return draftRef.current;
    const o = optsRef.current;
    if (!o?.treeId || !o?.parentNodeId) return null;
    const id = await prepareDraft(o.treeId, o.parentNodeId);
    if (id) {
      draftRef.current = id;
      setDraftNodeId(id);
    }
    return id;
  }, []);

  /** Upload files immediately to the draft workspace. */
  const uploadToDraft = useCallback(async (files: DroppedFile[]) => {
    const o = optsRef.current;
    if (!o?.treeId) return;
    const nodeId = await ensureDraft();
    if (!nodeId) return;
    setUploading(true);
    try {
      const result = await uploadFiles(o.treeId, nodeId, files);
      if (result) {
        const newQuotes = files.map((f) => ({
          node_id: nodeId,
          type: "file" as const,
          path: f.path,
        }));
        setUploadedQuotes((prev) => [...prev, ...newQuotes]);
      }
    } finally {
      setUploading(false);
    }
  }, [ensureDraft]);

  const addFromDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
    const { files } = await collectDroppedFiles(e);
    if (!files.length) return;
    setPendingFiles((prev) => [...prev, ...files]);
    if (canEagerUpload) {
      await uploadToDraft(files);
    }
  }, [canEagerUpload, uploadToDraft]);

  const addFromInput = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const fileList = e.target.files;
    if (!fileList) return;
    const newFiles: DroppedFile[] = [];
    for (let i = 0; i < fileList.length; i++) {
      const f = fileList[i];
      newFiles.push({ file: f, path: f.webkitRelativePath || f.name });
    }
    if (newFiles.length) {
      setPendingFiles((prev) => [...prev, ...newFiles]);
      if (canEagerUpload) {
        await uploadToDraft(newFiles);
      }
    }
    e.target.value = "";
  }, [canEagerUpload, uploadToDraft]);

  /** Handle paste events — extract images from the clipboard. */
  const addFromPaste = useCallback(async (e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const imageFiles: DroppedFile[] = [];
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.kind !== "file" || !item.type.startsWith("image/")) continue;
      const file = item.getAsFile();
      if (!file) continue;
      const ext = { "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp" }[item.type] || ".png";
      const name = `paste-${Date.now()}${ext}`;
      imageFiles.push({ path: name, file });
    }
    if (imageFiles.length === 0) return; // no images — let normal text paste through
    e.preventDefault();
    setPendingFiles((prev) => [...prev, ...imageFiles]);
    if (canEagerUpload) {
      await uploadToDraft(imageFiles);
    }
  }, [canEagerUpload, uploadToDraft]);

  const removeFile = useCallback((index: number) => {
    const file = pendingFiles[index];
    const remaining = pendingFiles.filter((_, i) => i !== index);
    setPendingFiles(remaining);
    setUploadedQuotes((prev) => prev.filter((_, i) => i !== index));

    // Delete from draft workspace on disk
    const o = optsRef.current;
    const draft = draftRef.current;
    if (draft && o?.treeId) {
      if (remaining.length === 0) {
        // No files left — discard the entire draft
        draftRef.current = null;
        setDraftNodeId(null);
        discardDraftApi(o.treeId, draft).catch(() => {});
      } else if (file) {
        // Delete just this file from the workspace
        fetch(`/api/trees/${o.treeId}/nodes/${draft}/files/${encodeURIComponent(file.path)}`, {
          method: "DELETE",
        }).catch(() => {});
      }
    }
  }, [pendingFiles]);

  const clearFiles = useCallback(() => {
    setPendingFiles([]);
    setUploadedQuotes([]);
  }, []);

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(true);
  }, []);

  const onDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
  }, []);

  /** Upload pending files and return file_quotes for the chat message.
   *  Used as fallback when eager upload is not available (no treeId/parentNodeId). */
  const uploadAndQuote = useCallback(async (
    treeId: string, nodeId: string,
  ): Promise<{
    quotes: Array<{ node_id: string; type: string; path: string }>;
    git_commit: string;
    count: number;
  } | null> => {
    // If eager upload already happened, return the accumulated quotes
    if (draftRef.current && uploadedQuotes.length > 0) {
      return { quotes: uploadedQuotes, git_commit: "", count: uploadedQuotes.length };
    }
    // Fallback: upload to the specified node (legacy path)
    const files = pendingFiles;
    if (files.length === 0) return null;
    const totalSize = files.reduce((sum, f) => sum + f.file.size, 0);
    if (totalSize > MAX_UPLOAD_BYTES) {
      alert(`Upload too large (${(totalSize / (1024 * 1024)).toFixed(1)}MB). Max is 50MB.`);
      return null;
    }
    setUploading(true);
    try {
      const result = await uploadFiles(treeId, nodeId, files);
      if (!result) return null;
      const quotes = files.map((f) => ({
        node_id: nodeId,
        type: "file" as const,
        path: f.path,
      }));
      setPendingFiles([]);
      return { quotes, git_commit: result.git_commit, count: result.count };
    } finally {
      setUploading(false);
    }
  }, [pendingFiles, uploadedQuotes]);

  /** Consume the draft — returns the draftNodeId and resets state.
   *  Called by handleSend so the draft isn't discarded on cleanup. */
  const consumeDraft = useCallback(() => {
    const id = draftRef.current;
    draftRef.current = null;
    setDraftNodeId(null);
    const quotes = [...uploadedQuotes];
    setUploadedQuotes([]);
    setPendingFiles([]);
    return { draftNodeId: id, uploadedQuotes: quotes };
  }, [uploadedQuotes]);

  /** Discard the current draft and its workspace. */
  const discardDraft = useCallback(async () => {
    const id = draftRef.current;
    const o = optsRef.current;
    if (id && o?.treeId) {
      draftRef.current = null;
      setDraftNodeId(null);
      setUploadedQuotes([]);
      setPendingFiles([]);
      await discardDraftApi(o.treeId, id).catch(() => {});
    }
  }, []);

  return {
    pendingFiles,
    uploading,
    dragOver,
    totalSize: pendingFiles.reduce((sum, f) => sum + f.file.size, 0),
    fileInputRef,
    addFromDrop,
    addFromInput,
    addFromPaste,
    removeFile,
    clearFiles,
    onDragOver,
    onDragLeave,
    uploadAndQuote,
    // Draft-related
    draftNodeId,
    uploadedQuotes,
    consumeDraft,
    discardDraft,
  };
}
