/* Shared header, footer and "back to top" button for every page.
   To change the menu, edit NAV below. Each page sets <body data-page="..."> to highlight its menu item. */
const NAV = [
  ["home", "Home", "index.html"],
  ["overview", "Overview", "overview.html"],
  ["principals", "Principals", "principals.html"],
  ["history", "History", "history.html"],
  ["identity", "Identity", "identity.html"],
  ["activities", "Student Activities", "activities.html"],
  ["notices", "Notice Board", "notices.html"],
  ["results", "Exam Results", "results.html"],
  ["tv", "TCC TV", "tcc-tv.html"],
  ["donations", "Donations", "donations.html"],
  ["more", "More", null, [["events", "Events", "events.html"], ["gallery", "Gallery", "gallery.html"], ["contact", "Contact", "index.html#contact"]]],
];
(function () {
  const page = document.body.dataset.page || "";
  const link = ([id, label, href]) => `<a href="${href}"${id === page ? ' aria-current="page"' : ""}>${label}</a>`;
  const nav = NAV.map((item) => item[3]
    ? `<div class="nav-drop${item[3].some((s) => s[0] === page) ? " is-current" : ""}"><button type="button" aria-expanded="false">${item[1]}</button><div class="nav-sub">${item[3].map(link).join("")}</div></div>`
    : link(item)).join("");

  document.body.insertAdjacentHTML("afterbegin", `
<header class="header">
  <div class="wrap">
    <a class="brand" href="index.html">
      <img src="assets/logo-color.webp" alt="School crest">
      <span><b>Thelijjawila Central College</b><span class="si">මාර/ තෙලිජ්ජවිල මධ්‍ය විද්‍යාලය</span></span>
    </a>
    <button class="menu-btn" id="menuBtn" aria-expanded="false" aria-controls="nav">Menu</button>
    <nav class="nav" id="nav">${nav}</nav>
  </div>
</header>`);

  document.body.insertAdjacentHTML("beforeend", `
<footer class="footer">
  <div class="wrap">
    <img src="assets/logo-color.webp" alt="">
    <span class="si">නාසති විද්‍යා සමං මිත්‍රම්</span>
    <small>National School since 1996 · Founded 1946</small>
    <div class="footer-social">
      <a href="https://www.facebook.com/thelijjawilacentralcollege" target="_blank" rel="noopener">Facebook</a>
      <a href="https://www.youtube.com/@ThelijjawilaCentralCollege" target="_blank" rel="noopener">YouTube</a>
    </div>
    <small>© ${new Date().getFullYear()} MR/ Thelijjawila Central College WEB DEVELOPMENT UNIT. All rights reserved. · <a href="admin.html">Admin</a></small>
  </div>
</footer>
<button class="to-top" id="toTop" aria-label="Go to top" hidden>↑</button>`);

  const menuBtn = document.getElementById("menuBtn"), navEl = document.getElementById("nav");
  menuBtn.addEventListener("click", () => menuBtn.setAttribute("aria-expanded", navEl.classList.toggle("open")));
  navEl.addEventListener("click", (e) => {
    const b = e.target.closest(".nav-drop > button");
    if (b) { const open = b.parentElement.classList.toggle("open"); b.setAttribute("aria-expanded", open); return; }
    if (e.target.tagName === "A") navEl.classList.remove("open");
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".nav-drop")) navEl.querySelectorAll(".nav-drop.open").forEach((d) => { d.classList.remove("open"); d.firstElementChild.setAttribute("aria-expanded", false); });
  });

  const top = document.getElementById("toTop");
  const onScroll = () => { top.hidden = window.scrollY < 500; };
  window.addEventListener("scroll", onScroll, { passive: true }); onScroll();
  top.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
})();
