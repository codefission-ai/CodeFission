import { marked } from "marked";
import katex from "katex";

marked.setOptions({ breaks: true, gfm: true });

const VIDEO_EXT = /\.(mp4|webm|mov|ogg)$/i;

// Custom renderer: convert image syntax with video extensions to <video>
const renderer = new marked.Renderer();
renderer.image = ({ href, title, text }: { href: string; title: string | null; text: string }) => {
  if (VIDEO_EXT.test(href)) {
    return `<video src="${href}" controls preload="metadata"${title ? ` title="${title}"` : ""}>${text}</video>`;
  }
  return `<img src="${href}" alt="${text}"${title ? ` title="${title}"` : ""} loading="lazy" />`;
};

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

export function renderMarkdown(text: string): string {
  try {
    return marked.parse(embedYouTube(renderLatex(text)), { renderer }) as string;
  } catch {
    return text.replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
}
