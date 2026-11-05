// Close PIN: shown when someone tries to close the program window.
(() => {
  const $ = (id) => document.getElementById(id);
  window.askClosePin = () => { const d = $("closeDlg"); if (!d.open) { $("closePin").value = ""; $("closeMsg").hidden = true; d.showModal(); $("closePin").focus(); } };
  $("closeCancel").onclick = () => $("closeDlg").close();
  $("closeForm").onsubmit = async (e) => {
    e.preventDefault();
    const pin = $("closePin").value;
    let ok = false;
    try {
      if (window.pywebview && window.pywebview.api && window.pywebview.api.quit) ok = await window.pywebview.api.quit(pin);
      else ok = (await fetch("/api/quit", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pin }) })).ok;
    } catch (err) {}
    if (!ok) { $("closeMsg").textContent = "PIN අංකය වැරදියි · Wrong PIN"; $("closeMsg").hidden = false; $("closePin").select(); }
  };
})();
