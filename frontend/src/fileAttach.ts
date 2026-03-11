/**
 * Shared file attachment utilities — drag-drop reading, upload, and the
 * useFileAttach hook used by every message input in the app.
 */

import { useState, useRef, useCallback } from "react";

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

// ── Format helpers ─────────────────────────────────────────────────────

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

// ── Hook ───────────────────────────────────────────────────────────────

export function useFileAttach() {
  const [pendingFiles, setPendingFiles] = useState<DroppedFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Ref keeps uploadAndQuote stable (no stale closure on pendingFiles).
  const pendingRef = useRef<DroppedFile[]>([]);
  pendingRef.current = pendingFiles;

  const addFromDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
    const { files } = await collectDroppedFiles(e);
    if (files.length) setPendingFiles((prev) => [...prev, ...files]);
  }, []);

  const addFromInput = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const fileList = e.target.files;
    if (!fileList) return;
    const newFiles: DroppedFile[] = [];
    for (let i = 0; i < fileList.length; i++) {
      const f = fileList[i];
      newFiles.push({ file: f, path: f.webkitRelativePath || f.name });
    }
    if (newFiles.length) setPendingFiles((prev) => [...prev, ...newFiles]);
    e.target.value = "";
  }, []);

  const removeFile = useCallback((index: number) => {
    setPendingFiles((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const clearFiles = useCallback(() => setPendingFiles([]), []);

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

  /** Upload pending files and return file_quotes for the chat message. */
  const uploadAndQuote = useCallback(async (
    treeId: string, nodeId: string,
  ): Promise<{
    quotes: Array<{ node_id: string; type: string; path: string }>;
    git_commit: string;
    count: number;
  } | null> => {
    const files = pendingRef.current;
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
  }, []);

  return {
    pendingFiles,
    uploading,
    dragOver,
    totalSize: pendingFiles.reduce((sum, f) => sum + f.file.size, 0),
    fileInputRef,
    addFromDrop,
    addFromInput,
    removeFile,
    clearFiles,
    onDragOver,
    onDragLeave,
    uploadAndQuote,
  };
}
