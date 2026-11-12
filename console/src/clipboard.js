/**
 * Copy text on localhost (secure context) and on plain HTTP.
 * Returns false when both the clipboard API and execCommand fail.
 */

export async function copyText(value) {
  const text = String(value ?? "");
  if (window.isSecureContext && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Fall through to the selection fallback.
    }
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.readOnly = true;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.top = "0";
  area.style.left = "0";
  area.style.width = "2em";
  area.style.height = "2em";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.focus();
  area.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  area.remove();
  return Boolean(ok);
}
