// Pulls the latest posts from the school's Facebook Page and writes data/posts.json.
// Needs Node 18+. Run:  FB_PAGE_ID=... FB_PAGE_TOKEN=... node scripts/fetch-facebook.mjs
// The token must be a Page access token for the school's page (see SETUP-FACEBOOK.md).
// Never put the token inside the website files — keep it as a secret.
import { readFile, writeFile } from "node:fs/promises";

const clean = (v) => (v || "").trim().replace(/^["']|["']$/g, "");
const PAGE_ID = clean(process.env.FB_PAGE_ID);
const TOKEN = clean(process.env.FB_PAGE_TOKEN);
const MAX_POSTS = Number(process.env.FB_MAX_POSTS || 60);
const API = "https://graph.facebook.com/v21.0";
const root = new URL("..", import.meta.url);

if (!PAGE_ID || !TOKEN) {
  console.error("Missing FB_PAGE_ID or FB_PAGE_TOKEN.");
  process.exit(1);
}
// Safe checks (never prints the token itself)
console.log(`Page ID looks ${/^\d+$/.test(PAGE_ID) ? "OK (numbers only)" : "WRONG (should be numbers only)"}, length ${PAGE_ID.length}`);
console.log(`Token length ${TOKEN.length}, starts with "EAA": ${TOKEN.startsWith("EAA")}, has spaces/new lines: ${/\s/.test(TOKEN)}`);

const rules = JSON.parse(await readFile(new URL("data/categories.json", root), "utf8"));

function categorise(text) {
  const t = (text || "").toLowerCase();
  for (const [tag, cat] of Object.entries(rules.hashtags)) {
    if (!tag.startsWith("_") && t.includes(tag)) return cat;
  }
  for (const c of rules.categories) {
    if (c.keywords.some((k) => t.includes(k.toLowerCase()))) return c.name;
  }
  return "General";
}

const fields = "id,message,created_time,permalink_url,full_picture,attachments{media_type,media,subattachments.limit(4){media}}";
let url = `${API}/${PAGE_ID}/posts?fields=${encodeURIComponent(fields)}&limit=25&access_token=${TOKEN}`;
const posts = [];

while (url && posts.length < MAX_POSTS) {
  const res = await fetch(url);
  const body = await res.json();
  if (!res.ok || body.error) {
    console.error("Facebook API error:", body.error?.message || res.status);
    process.exit(1);
  }
  for (const p of body.data) {
    if (!p.message && !p.full_picture) continue; // skip empty shares
    const extra = (p.attachments?.data?.[0]?.subattachments?.data || [])
      .map((s) => s.media?.image?.src).filter(Boolean);
    posts.push({
      id: p.id,
      date: p.created_time,
      text: p.message || "",
      image: p.full_picture || null,
      images: extra,
      link: p.permalink_url,
      category: categorise(p.message),
    });
    if (posts.length >= MAX_POSTS) break;
  }
  url = body.paging?.next || null;
}

const out = { updated: new Date().toISOString(), source: "facebook", posts };
await writeFile(new URL("data/posts.json", root), JSON.stringify(out, null, 2) + "\n");
console.log(`Saved ${posts.length} posts to data/posts.json`);
