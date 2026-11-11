// navigator.clipboard only exists on secure origins (https, localhost). The
// real-world install is plain http on a LAN IP (PROBLEMS.md #5), where it is
// undefined, so calling it directly threw before any .catch could run. The
// fallback copies through a hidden <textarea> + execCommand("copy"), which
// is deprecated but still the only option browsers offer an insecure page.
//
// Resolves true when the text reached the clipboard, false when both paths
// failed (the caller then tells the user to copy by hand).
export async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Permission denied or document not focused — try the fallback.
    }
  }
  return legacyCopy(text);
}

function legacyCopy(text) {
  // Inside a modal <dialog> everything outside it is inert, so a textarea
  // appended to <body> can't be selected — host it in the open dialog.
  const previousFocus = document.activeElement;
  const host = previousFocus?.closest?.("dialog[open]") ?? document.body;

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.top = "0";
  textarea.style.left = "0";
  textarea.style.opacity = "0";
  textarea.style.pointerEvents = "none";
  host.appendChild(textarea);

  const selection = document.getSelection();
  const previousRange = selection && selection.rangeCount > 0 ? selection.getRangeAt(0) : null;

  textarea.select();
  const copied = execCopy();
  host.removeChild(textarea);

  if (previousRange && selection) {
    selection.removeAllRanges();
    selection.addRange(previousRange);
  }
  if (previousFocus && typeof previousFocus.focus === "function") previousFocus.focus();
  return copied;
}

function execCopy() {
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  }
}
