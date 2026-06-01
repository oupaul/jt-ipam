/**
 * 極簡、零相依的 Markdown → HTML(給 AI chat 本地 LLM 回應用)。
 *
 * 安全性：先把 & < > 跳脫，之後才插入我們自己產生的標籤，因此不會有 HTML 注入。
 * 只支援聊天常見語法：標題 / 粗體 / 斜體 / 行內 code / code fence / 連結 /
 * 有序與無序清單 / 段落與換行。不是完整 CommonMark，夠用即可。
 */

function escapeHtml(s: string): string {
  // 連引號一起跳脫，避免惡意 LLM/工具回應用 [x](https://a"onx=...) 之類做屬性注入 XSS
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function inline(s: string): string {
  // 行內 code(先處理，避免內部被其它規則動到)
  s = s.replace(/`([^`]+)`/g, (_m, c) => `<code>${c}</code>`);
  // 連結 [text](http...)
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    (_m, t, u) => `<a href="${u}" target="_blank" rel="noopener noreferrer">${t}</a>`);
  // 粗體 **x**
  s = s.replace(/\*\*([^*]+)\*\*/g, (_m, c) => `<strong>${c}</strong>`);
  // 斜體 *x* 或 _x_
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, (_m, p, c) => `${p}<em>${c}</em>`);
  s = s.replace(/(^|[^_])_([^_\n]+)_/g, (_m, p, c) => `${p}<em>${c}</em>`);
  return s;
}

export function renderMarkdown(src: string): string {
  if (!src) return "";
  const text = escapeHtml(src.replace(/\r\n/g, "\n"));
  const lines = text.split("\n");
  const out: string[] = [];

  let inCode = false;
  let codeBuf: string[] = [];
  let listType: "ul" | "ol" | null = null;
  let para: string[] = [];

  const flushPara = () => {
    if (para.length) {
      out.push(`<p>${inline(para.join(" "))}</p>`);
      para = [];
    }
  };
  const closeList = () => {
    if (listType) { out.push(`</${listType}>`); listType = null; }
  };

  for (const raw of lines) {
    const line = raw;

    // code fence
    if (/^\s*```/.test(line)) {
      if (inCode) {
        out.push(`<pre><code>${codeBuf.join("\n")}</code></pre>`);
        codeBuf = []; inCode = false;
      } else {
        flushPara(); closeList(); inCode = true;
      }
      continue;
    }
    if (inCode) { codeBuf.push(line); continue; }

    // 空行 → 段落 / 清單分界
    if (!line.trim()) { flushPara(); closeList(); continue; }

    // 標題
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      flushPara(); closeList();
      const lvl = h[1].length;
      out.push(`<h${lvl}>${inline(h[2])}</h${lvl}>`);
      continue;
    }

    // 有序清單
    const ol = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (ol) {
      flushPara();
      if (listType !== "ol") { closeList(); out.push("<ol>"); listType = "ol"; }
      out.push(`<li>${inline(ol[1])}</li>`);
      continue;
    }
    // 無序清單
    const ul = /^\s*[-*+]\s+(.*)$/.exec(line);
    if (ul) {
      flushPara();
      if (listType !== "ul") { closeList(); out.push("<ul>"); listType = "ul"; }
      out.push(`<li>${inline(ul[1])}</li>`);
      continue;
    }

    // 一般段落行
    closeList();
    para.push(line.trim());
  }

  if (inCode) out.push(`<pre><code>${codeBuf.join("\n")}</code></pre>`);
  flushPara();
  closeList();
  return out.join("\n");
}
