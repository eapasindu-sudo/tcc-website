// Pulls the latest videos from the school's YouTube channel (TCC TV) and writes data/videos.json.
// No key needed: it reads the channel's public RSS feed.
import { writeFile } from "node:fs/promises";

const HANDLE = process.env.YT_HANDLE || "@ThelijjawilaCentralCollege";
const root = new URL("..", import.meta.url);
const UA = { "User-Agent": "Mozilla/5.0 (school-site-sync)", "Accept-Language": "en" };

let channelId = process.env.YT_CHANNEL_ID || "";
if (!channelId) {
  const html = await (await fetch(`https://www.youtube.com/${HANDLE}`, { headers: UA })).text();
  channelId = (html.match(/"externalId":"(UC[\w-]{22})"/) || html.match(/channel\/(UC[\w-]{22})/) || [])[1] || "";
}
if (!channelId) { console.error("Could not find the channel id."); process.exit(1); }
console.log("Channel", channelId);

const xml = await (await fetch(`https://www.youtube.com/feeds/videos.xml?channel_id=${channelId}`, { headers: UA })).text();
const decode = (s) => s.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'");
const videos = [...xml.matchAll(/<entry>([\s\S]*?)<\/entry>/g)].map(([, e]) => ({
  id: (e.match(/<yt:videoId>(.*?)<\/yt:videoId>/) || [])[1],
  title: decode((e.match(/<title>([\s\S]*?)<\/title>/) || [])[1] || ""),
  date: (e.match(/<published>(.*?)<\/published>/) || [])[1],
  description: decode((e.match(/<media:description>([\s\S]*?)<\/media:description>/) || [])[1] || "").slice(0, 400),
})).filter((v) => v.id);

if (!videos.length) { console.error("No videos found in the feed."); process.exit(1); }
await writeFile(new URL("data/videos.json", root), JSON.stringify({ updated: new Date().toISOString(), channel: channelId, videos }, null, 2) + "\n");
console.log(`Saved ${videos.length} videos.`);
