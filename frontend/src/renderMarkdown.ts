import { marked } from "marked";
import katex from "katex";

marked.setOptions({ breaks: true, gfm: true });

/**
 * Render LaTeX blocks ($$..$$) and inline ($...$) before passing to marked.
 * Uses KaTeX for rendering.
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
    return marked.parse(renderLatex(text)) as string;
  } catch {
    return text.replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
}
