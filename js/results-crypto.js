/* Shared by results.html and admin.html.
   Each student's marks are encrypted with a key made from their own details (admission number, ID number, date of birth),
   so the published files can't be read without those details. */
const RC = {
  ITER: 100000,
  norm: {
    adm: (v) => String(v ?? "").replace(/\s+/g, "").replace(/^0+(?=\d)/, "").toUpperCase(),
    nic: (v) => String(v ?? "").replace(/\s+/g, "").toUpperCase(),
    dob: (v) => {
      if (v == null || v === "") return "";
      if (typeof v === "number") { const d = new Date(Math.round((v - 25569) * 864e5)); return d.toISOString().slice(0, 10); } // Excel date number
      const s = String(v).trim();
      let m = s.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/); if (m) return `${m[1]}-${m[2].padStart(2, "0")}-${m[3].padStart(2, "0")}`;
      m = s.match(/^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})/); if (m) return `${m[3]}-${m[2].padStart(2, "0")}-${m[1].padStart(2, "0")}`; // DD/MM/YYYY
      return s;
    },
  },
  material(fields, vals) { return fields.map((f) => RC.norm[f](vals[f])).join("|"); },
  b64: (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))),
  unb64: (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0)),
  async lookupId(salt, material) {
    const h = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(salt + "|" + material));
    return [...new Uint8Array(h)].slice(0, 16).map((b) => b.toString(16).padStart(2, "0")).join("");
  },
  async key(salt, material, iter) {
    const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(material), "PBKDF2", false, ["deriveKey"]);
    return crypto.subtle.deriveKey({ name: "PBKDF2", salt: new TextEncoder().encode(salt), iterations: iter, hash: "SHA-256" }, base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  },
  async seal(salt, material, obj, iter = RC.ITER) {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, await RC.key(salt, material, iter), new TextEncoder().encode(JSON.stringify(obj)));
    return { iv: RC.b64(iv), ct: RC.b64(ct) };
  },
  async open(salt, material, rec, iter = RC.ITER) {
    const pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv: RC.unb64(rec.iv) }, await RC.key(salt, material, iter), RC.unb64(rec.ct));
    return JSON.parse(new TextDecoder().decode(pt));
  },
};
