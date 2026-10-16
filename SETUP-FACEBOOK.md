# Showing Facebook posts on the website automatically

The "News from Facebook" section reads `data/posts.json`. A small script
(`scripts/fetch-facebook.mjs`) downloads the newest posts from the school's
Facebook page into that file and sorts each post into a category. A GitHub
Action (`.github/workflows/facebook-sync.yml`) runs the script every 2 hours,
so new posts appear on the site without anyone touching the code.

Facebook only gives a page's posts to someone who is an **admin of that page**,
so these steps must be done by (or with) a page admin.

## 1. Get a Page ID and a Page access token
1. Go to https://developers.facebook.com, log in with the admin's account, and
   create an app (type: **Business**).
2. Open **Tools → Graph API Explorer**. Choose your app, then
   **Get Token → Get Page Access Token**, and pick the school page.
   Allow the permissions `pages_read_engagement` and `pages_show_list`.
3. Turn it into a long-lived token: open **Tools → Access Token Debugger**,
   paste the token, and click **Extend Access Token**. Then in the Graph API
   Explorer run `me/accounts` with that long-lived user token; the
   `access_token` shown for the school page does not expire.
4. The page's `id` is shown in the same `me/accounts` result.

Keep the token secret. Never paste it into `index.html` or `main.js`.

## 2. Put the website on GitHub Pages
1. Create a GitHub repository and upload everything in this folder.
2. **Settings → Pages**: set **Source** to **GitHub Actions**.
3. **Settings → Secrets and variables → Actions**: add two secrets,
   `FB_PAGE_ID` and `FB_PAGE_TOKEN`.
4. **Actions → Update Facebook posts and publish site → Run workflow** to fetch posts the first time.

After that it runs by itself every 2 hours.

## 3. Categories
Edit `data/categories.json` to change how posts are sorted. A post goes into the
first category whose keywords (English or Sinhala) appear in it. Admins can also
force a category by adding a hashtag to the Facebook post, such as `#Sports`,
`#Events`, `#Achievements`, `#Academic`, `#Religious` or `#Notice`.
Posts that match nothing are shown under **General**.

## Running it on your own computer (optional)
With Node.js 18 or newer:

    FB_PAGE_ID=1234567890 FB_PAGE_TOKEN=xxxx node scripts/fetch-facebook.mjs
