import { marked } from "marked";
import katex from "katex";

marked.setOptions({ breaks: true, gfm: true });

const VIDEO_EXT = /\.(mp4|webm|mov|ogg)$/i;

function rewriteSrc(href: string, nodeId?: string): string {
  if (!nodeId || !href) return href;
  if (href.startsWith("http://") || href.startsWith("https://") || href.startsWith("data:")) return href;
  // Strip leading ./ or /
  const rel = href.replace(/^\.\//, "").replace(/^\//, "");
  return `/api/files/${nodeId}/${rel}`;
}

// Custom renderer: convert image syntax with video extensions to <video>
// Rewrites relative paths to serve through the node's file API
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const _rendererCache = new Map<string, any>();

function getRenderer(nodeId?: string) {
  const key = nodeId || "";
  let renderer = _rendererCache.get(key);
  if (renderer) return renderer;

  renderer = new marked.Renderer();
  renderer.image = ({ href, title, text }: { href: string; title: string | null; text: string }) => {
    const src = rewriteSrc(href, nodeId);
    if (VIDEO_EXT.test(href)) {
      return `<video src="${src}" controls preload="metadata"${title ? ` title="${title}"` : ""}>${text}</video>`;
    }
    return `<img src="${src}" alt="${text}"${title ? ` title="${title}"` : ""} loading="lazy" />`;
  };
  _rendererCache.set(key, renderer);
  return renderer;
}

// Auto-embed YouTube links (standalone on their own line)
function embedYouTube(text: string): string {
  return text.replace(
    /^(https?:\/\/(?:www\.)?(?:youtube\.com\/watch\?v=|youtu\.be\/)([\w-]+)(?:[&?][\w=&]*)?)$/gm,
    (_, _url, id) => `<div class="video-embed"><iframe src="https://www.youtube.com/embed/${id}" frameborder="0" allowfullscreen loading="lazy"></iframe></div>`
  );
}

/**
 * Render LaTeX blocks ($$..$$) and inline ($...$) before passing to marked.
 */
function renderLatex(text: string): string {
  // Block math: $$ ... $$
  text = text.replace(/\$\$([\s\S]+?)\$\$/g, (_, tex) => {
    try {
      return katex.renderToString(tex.trim(), { displayMode: true, throwOnError: false });
    } catch {
      return `<pre>${tex}</pre>`;
    }
  });
  // Inline math: $ ... $ (not inside code)
  text = text.replace(/(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)/g, (_, tex) => {
    try {
      return katex.renderToString(tex.trim(), { displayMode: false, throwOnError: false });
    } catch {
      return `<code>${tex}</code>`;
    }
  });
  return text;
}

export function renderMarkdown(text: string, nodeId?: string): string {
  try {
    return marked.parse(embedYouTube(renderLatex(text)), { renderer: getRenderer(nodeId) }) as string;
  } catch {
    return text.replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
}
