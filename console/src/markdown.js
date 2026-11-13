/**
 * Chat bubbles render Markdown at display time.
 * The thread keeps the raw string. `renderChatMarkdown` is the only path
 * that may produce HTML, and that HTML is sanitized before it returns.
 */

import { Marked } from "marked";
import sanitizeHtml from "sanitize-html";

const ALLOWED_TAGS = [
  "p",
  "br",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "em",
  "strong",
  "ul",
  "ol",
  "li",
  "blockquote",
  "code",
  "pre",
  "a",
  "del",
  "table",
  "thead",
  "tbody",
  "tfoot",
  "tr",
  "th",
  "td",
];

const RAW_CONTAINER = /<(script|style|iframe|object|embed|noscript|textarea)\b[^>]*>[\s\S]*?(?:<\/\1\s*>|$)/gi;
const CODE_SPAN = /```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]+`/g;

function stripRawContainers(value) {
  const blocks = [];
  const hidden = value.replace(CODE_SPAN, (match) => {
    const token = `\uE000${blocks.length}\uE001`;
    blocks.push(match);
    return token;
  });
  const stripped = hidden.replace(RAW_CONTAINER, "");
  return stripped.replace(/\uE000(\d+)\uE001/g, (_match, index) => blocks[Number(index)] ?? "");
}

const parser = new Marked({
  async: false,
  gfm: true,
  breaks: true,
  renderer: {
    html() {
      return "";
    },
    image({ text } = {}) {
      return escapeText(text || "");
    },
  },
});

function escapeText(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function cleanUrl(value) {
  return String(value)
    .replace(/[\u0000-\u0020]+/g, "")
    .replace(/<!--[\s\S]*?-->/g, "");
}

function isHttpUrl(value) {
  return /^https?:\/\//i.test(cleanUrl(value));
}

function sanitizeChatHtml(unsafe) {
  return sanitizeHtml(unsafe, {
    allowedTags: ALLOWED_TAGS,
    allowedAttributes: {
      a: ["href", "target", "rel"],
    },
    allowedSchemes: ["http", "https"],
    allowedSchemesByTag: { a: ["http", "https"] },
    allowedSchemesAppliedToAttributes: ["href"],
    allowProtocolRelative: false,
    disallowedTagsMode: "discard",
    transformTags: {
      a(_tagName, attribs) {
        const href = typeof attribs.href === "string" ? attribs.href : "";
        if (!isHttpUrl(href)) {
          return { tagName: "span", attribs: {} };
        }
        return {
          tagName: "a",
          attribs: {
            href,
            target: "_blank",
            rel: "noopener noreferrer",
          },
        };
      },
    },
  });
}

export function renderChatMarkdown(text) {
  const source = typeof text === "string" ? text : "";
  if (!source) {
    return "";
  }
  let parsed = "";
  try {
    const raw = parser.parse(stripRawContainers(source));
    parsed = typeof raw === "string" ? raw : "";
  } catch {
    parsed = `<p>${escapeText(source)}</p>`;
  }
  return sanitizeChatHtml(parsed);
}
