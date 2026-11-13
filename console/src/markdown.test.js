import assert from "node:assert/strict";
import { test } from "node:test";

import { renderChatMarkdown } from "./markdown.js";

test("renderChatMarkdown renders headings, lists, emphasis, code, and https links", () => {
  const html = renderChatMarkdown(
    [
      "# 标题",
      "",
      "- 一",
      "- 二",
      "",
      "*斜体* **加粗**",
      "",
      "`行内`",
      "",
      "```",
      "const n = 1;",
      "```",
      "",
      "[文档](https://example.com/docs)",
      "",
      "> 引用",
    ].join("\n"),
  );
  assert.match(html, /<h1>标题<\/h1>/);
  assert.match(html, /<ul>\s*<li>一<\/li>\s*<li>二<\/li>\s*<\/ul>/);
  assert.match(html, /<em>斜体<\/em>/);
  assert.match(html, /<strong>加粗<\/strong>/);
  assert.match(html, /<code>行内<\/code>/);
  assert.match(html, /<pre><code>const n = 1;\s*<\/code><\/pre>/);
  assert.match(html, /<blockquote>\s*<p>引用<\/p>\s*<\/blockquote>/);
  assert.match(
    html,
    /<a href="https:\/\/example\.com\/docs" target="_blank" rel="noopener noreferrer">文档<\/a>/,
  );
  assert.match(renderChatMarkdown("普通文本"), /^<p>普通文本<\/p>\s*$/);
  assert.match(renderChatMarkdown("你好\n第二行"), /<br\s*\/?>/);
  const gfm = renderChatMarkdown("~~删掉~~\n\n| 甲 | 乙 |\n| --- | --- |\n| 1 | 2 |");
  assert.match(gfm, /<del>删掉<\/del>/);
  assert.match(gfm, /<table>/);
  assert.match(gfm, /<td>1<\/td>/);
});

test("scripts, event attributes, and javascript or data urls are stripped", () => {
  const script = renderChatMarkdown("<script>alert(1)</script>");
  assert.equal(script.includes("<script"), false);
  assert.equal(script.includes("alert(1)"), false);
  assert.equal(script.includes("<script>alert(1)</script>"), false);

  const image = renderChatMarkdown('<img src=x onerror="alert(1)">');
  assert.equal(image.includes("<img"), false);
  assert.equal(image.includes("onerror"), false);

  const handler = renderChatMarkdown('<p onclick="alert(1)">hi</p>');
  assert.equal(/\son[a-z]+\s*=/i.test(handler), false);
  assert.equal(handler.includes("onclick"), false);

  const javascript = renderChatMarkdown("[点我](javascript:alert(1))");
  assert.equal(/javascript:/i.test(javascript), false);
  assert.match(javascript, /点我/);
  assert.equal(/<a\b/i.test(javascript), false);

  const data = renderChatMarkdown("[点我](data:text/html,hi)");
  assert.equal(/data:/i.test(data), false);
  assert.equal(/<a\b/i.test(data), false);

  const mixed = renderChatMarkdown("**ok** <script>alert(1)</script>");
  assert.match(mixed, /<strong>ok<\/strong>/);
  assert.equal(mixed.includes("<script"), false);
  assert.equal(mixed.includes("alert(1)"), false);
});

test("unsanitized html does not appear as-is", () => {
  const raw = '<script>alert(1)</script><img src=x onerror=alert(1)><strong>原文</strong>';
  const html = renderChatMarkdown(raw);
  assert.equal(html.includes(raw), false);
  assert.equal(html.includes("<script"), false);
  assert.equal(html.includes("<img"), false);
  assert.equal(html.includes("onerror"), false);
  assert.equal(html.includes("<strong>原文</strong>"), false);

  const coded = renderChatMarkdown("```\n<script>alert(1)</script>\n```");
  assert.match(coded, /<pre><code>&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.equal(coded.includes("<script>"), false);

  const https = renderChatMarkdown("[文档](HTTPS://example.com/a)");
  assert.match(https, /href="HTTPS:\/\/example\.com\/a"/);
  assert.match(https, /target="_blank"/);
  assert.match(https, /rel="noopener noreferrer"/);
  assert.equal(/<a\b/i.test(renderChatMarkdown("[相对](/chat)")), false);
  assert.equal(/<a\b/i.test(renderChatMarkdown("[协议相对](//example.com)")), false);
  assert.equal(renderChatMarkdown("![图](https://example.com/a.png)").includes("<img"), false);
  assert.equal(renderChatMarkdown(null), "");
  assert.equal(renderChatMarkdown(""), "");
});
