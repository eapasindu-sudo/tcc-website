/* School events are managed on the admin page (admin.html) and saved in data/events.json.
   Event fields: title, date ("YYYY-MM-DD"), time, venue, category, summary, details,
   schedule ([["8:00 AM", "Opening"], ...]), who, contact. Past events move to "Past events" automatically. */
let EVENTS = [];

/* ---------- Loading screen ---------- */
const loader = document.getElementById("loader");
function hideLoader() { if (loader) loader.classList.add("done"); }
if (loader) {
  loader.addEventListener("click", hideLoader);
  loader.addEventListener("animationend", (e) => { if (e.target === loader) loader.hidden = true; });
}

/* ---------- Mobile menu ---------- */
const menuBtn = document.getElementById("menuBtn");
const nav = document.getElementById("nav");
menuBtn.addEventListener("click", () => {
  const open = nav.classList.toggle("open");
  menuBtn.setAttribute("aria-expanded", open);
});
nav.addEventListener("click", (e) => { if (e.target.tagName === "A") nav.classList.remove("open"); });

/* ---------- Hero video: show it only if the file loads ---------- */
const hero = document.querySelector(".hero");
const video = document.getElementById("heroVideo");
if (video) {
  video.addEventListener("loadeddata", () => hero.classList.add("has-video"));
  video.addEventListener("error", () => video.remove(), true);
}

/* ---------- Events ---------- */
const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const today = new Date(); today.setHours(0, 0, 0, 0);
const listEl = document.getElementById("eventList");
const dlg = document.getElementById("eventDialog");
let state = { cat: "All", when: "upcoming" };

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const parse = (d) => { const [y, m, day] = d.split("-").map(Number); return new Date(y, m - 1, day); };

function render() {
  const items = EVENTS
    .map((e, i) => ({ ...e, i, d: parse(e.date) }))
    .filter((e) => (state.when === "upcoming" ? e.d >= today : e.d < today))
    .filter((e) => state.cat === "All" || e.category === state.cat)
    .sort((a, b) => (state.when === "upcoming" ? a.d - b.d : b.d - a.d));

  listEl.innerHTML = items.length ? items.map((e) => `
    <article class="ev ${e.d < today ? "past" : ""}">
      <div class="ev__date"><b>${e.d.getDate()}</b><span>${MONTHS[e.d.getMonth()]}</span><small>${e.d.getFullYear()}</small></div>
      <div>
        <span class="cat">${esc(e.category)}</span>
        <h3>${esc(e.title)}</h3>
        <div class="meta"><span>${esc(e.time)}</span><span>${esc(e.venue)}</span></div>
        <p class="sum">${esc(e.summary)}</p>
        <button class="more" data-i="${e.i}">View details</button>
      </div>
    </article>`).join("")
    : `<p class="empty">${EVENTS.length ? `No ${state.when} events in this category.` : "No events have been announced yet. Follow our Facebook page for the latest news."}</p>`;
}

function openEvent(i) {
  const e = EVENTS[i], d = parse(e.date);
  const longDate = d.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  dlg.innerHTML = `
    <div class="dlg-top">
      <span class="cat">${esc(e.category)}</span>
      <h3>${esc(e.title)}</h3>
      <button class="dlg-close" aria-label="Close">✕</button>
    </div>
    <div class="dlg-body">
      <div class="dlg-facts">
        <div><small>Date</small>${esc(longDate)}</div>
        <div><small>Time</small>${esc(e.time)}</div>
        <div><small>Venue</small>${esc(e.venue)}</div>
        ${e.who ? `<div><small>Who can attend</small>${esc(e.who)}</div>` : ""}
      </div>
      ${e.details ? `<p style="white-space:pre-line">${esc(e.details)}</p>` : ""}
      ${e.schedule && e.schedule.length ? `<div><h4>Programme</h4><ul class="sched">${e.schedule.map(([t, s]) => `<li><b>${esc(t)}</b><span>${esc(s)}</span></li>`).join("")}</ul></div>` : ""}
      ${e.contact ? `<p style="color:var(--muted);font-size:.9rem">For more details contact: <strong>${esc(e.contact)}</strong></p>` : ""}
    </div>`;
  dlg.showModal();
  dlg.querySelector(".dlg-close").addEventListener("click", () => dlg.close());
}
dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
listEl.addEventListener("click", (e) => { const b = e.target.closest(".more"); if (b) openEvent(+b.dataset.i); });

document.getElementById("catChips").addEventListener("click", (e) => {
  const b = e.target.closest(".chip"); if (!b) return;
  state.cat = b.dataset.cat;
  document.querySelectorAll("#catChips .chip").forEach((c) => c.setAttribute("aria-pressed", c === b));
  render();
});
document.getElementById("whenSeg").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  state.when = b.dataset.when;
  document.querySelectorAll("#whenSeg button").forEach((c) => c.setAttribute("aria-pressed", c === b));
  render();
});
render();

/* ---------- News from Facebook ----------
   Reads data/posts.json (kept up to date by scripts/fetch-facebook.mjs).
   If that file can't be loaded (e.g. opening index.html straight from disk), the sample posts below are shown. */
const NEWS_CATS = ["All", "Events", "Achievements", "Sports", "Aesthetics", "Academic", "Religious", "Notices", "General"];
const SAMPLE_POSTS = {"updated": "2026-10-16T08:00:00Z", "source": "sample", "posts": [{"id": "s1", "date": "2026-10-14T10:20:00+05:30", "text": "Congratulations to our under-17 volleyball team on winning the zonal championship! 🏐 Thank you coaches and parents for the support. #Achievements", "image": null, "images": [], "link": "https://www.facebook.com/thelijjawilacentralcollege", "category": "Achievements"}, {"id": "s2", "date": "2026-10-06T13:05:00+05:30", "text": "ගුරු දින සැමරුම 2026. අපගේ ගුරුවරුන්ට ගෞරව දැක්වීම සඳහා ශිෂ්‍ය නායක මණ්ඩලය විසින් සංවිධානය කළ උත්සවයේ ඡායාරූප.", "image": null, "images": [], "link": "https://www.facebook.com/thelijjawilacentralcollege", "category": "Events"}, {"id": "s3", "date": "2026-10-02T08:40:00+05:30", "text": "Notice to parents: The Grade 11 parent–teacher meeting will be held on Saturday 31 October at 9.00 AM in the main hall.", "image": null, "images": [], "link": "https://www.facebook.com/thelijjawilacentralcollege", "category": "Notices"}, {"id": "s4", "date": "2026-09-27T16:10:00+05:30", "text": "අන්තර් නිවාස ක්‍රිකට් තරඟාවලියේ අවසන් තරඟය හෙට පාසල් ක්‍රීඩාංගණයේදී. සැමට සහභාගි වන්න.", "image": null, "images": [], "link": "https://www.facebook.com/thelijjawilacentralcollege", "category": "Sports"}, {"id": "s5", "date": "2026-09-18T11:00:00+05:30", "text": "Grade 11 O/L seminar series begins this week. Model papers will be given to all students. #Academic", "image": null, "images": [], "link": "https://www.facebook.com/thelijjawilacentralcollege", "category": "Academic"}, {"id": "s6", "date": "2026-09-10T07:30:00+05:30", "text": "පාසලේ වාර්ෂික බෝධි පූජා පින්කම සහ පිරිත් දේශනාව. පින් අනුමෝදන් වන්න.", "image": null, "images": [], "link": "https://www.facebook.com/thelijjawilacentralcollege", "category": "Religious"}]};
const newsList = document.getElementById("newsList");
const newsChips = document.getElementById("newsChips");
const newsMore = document.getElementById("newsMore");
let news = { posts: [], cat: "All", shown: 6 };

function timeAgo(iso) {
  const d = new Date(iso), days = Math.floor((Date.now() - d) / 864e5);
  if (days < 1) return "Today";
  if (days < 2) return "Yesterday";
  if (days < 7) return days + " days ago";
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

function renderNews() {
  const present = new Set(news.posts.map((p) => p.category));
  newsChips.innerHTML = NEWS_CATS.filter((c) => c === "All" || present.has(c))
    .map((c) => `<button class="chip" data-cat="${c}" aria-pressed="${c === news.cat}">${c}</button>`).join("");
  const list = news.posts.filter((p) => news.cat === "All" || p.category === news.cat);
  const logo = document.querySelector(".brand img").src;
  newsList.innerHTML = list.slice(0, news.shown).map((p) => `
    <article class="post">
      ${p.image ? `<img class="post__img" src="${esc(p.image)}" alt="" loading="lazy">` : `<div class="post__ph"><img src="${logo}" alt=""></div>`}
      <div class="post__body">
        <div class="post__top"><span class="cat">${esc(p.category)}</span><time datetime="${esc(p.date)}">${timeAgo(p.date)}</time></div>
        <p class="post__text clamp">${esc(p.text)}</p>
        <div class="post__actions">
          ${p.text.length > 220 ? `<button data-expand>Read more</button>` : ""}
          <a href="${esc(p.link)}" target="_blank" rel="noopener">View on Facebook</a>
        </div>
      </div>
    </article>`).join("") || `<p class="empty">No posts in this category yet.</p>`;
  newsMore.hidden = list.length <= news.shown;
}
newsChips.addEventListener("click", (e) => {
  const b = e.target.closest(".chip"); if (!b) return;
  news.cat = b.dataset.cat; news.shown = 6; renderNews();
});
newsList.addEventListener("click", (e) => {
  const b = e.target.closest("[data-expand]"); if (!b) return;
  const t = b.closest(".post").querySelector(".post__text");
  const open = t.classList.toggle("clamp");
  b.textContent = open ? "Read more" : "Show less";
});
newsMore.addEventListener("click", () => { news.shown += 6; renderNews(); });

/* Gallery: newest Facebook photos */
function renderGallery(posts) {
  const grid = document.getElementById("galleryGrid");
  const pics = posts.filter((p) => p.image).slice(0, 7);
  if (!grid || pics.length < 4) return; // keep the placeholders
  grid.innerHTML = pics.map((p) => {
    const cap = (p.text.split("\n").find((l) => l.trim()) || "").replace(/[#@]\S+/g, "").trim();
    const short = cap.length > 70 ? cap.slice(0, 68) + "…" : cap;
    return `<figure><a href="${esc(p.link)}" target="_blank" rel="noopener">
      <img src="${esc(p.image)}" alt="${esc(short)}" loading="lazy">
      <figcaption>${esc(short)}</figcaption></a></figure>`;
  }).join("");
}

function loadNews(data) {
  news.posts = (data.posts || []).slice().sort((a, b) => new Date(b.date) - new Date(a.date));
  const upd = document.getElementById("newsUpdated");
  upd.textContent = data.source === "sample" ? "Showing sample posts" : "Updated " + timeAgo(data.updated);
  renderNews();
  renderGallery(news.posts);
}
fetch("data/posts.json", { cache: "no-cache" })
  .then((r) => (r.ok ? r.json() : Promise.reject()))
  .then(loadNews)
  .catch(() => loadNews(SAMPLE_POSTS));

document.getElementById("year").textContent = new Date().getFullYear();
