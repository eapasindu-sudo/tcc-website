# Adding school events (admin page)

Events on the website are managed at **https://eapasindu-sudo.github.io/tcc-website/admin.html**
(there is also a small "Admin" link at the bottom of the website).

## One-time setup: create an access token
The admin page saves events straight into the website files on GitHub, so it needs a
GitHub access token from the account that owns the `tcc-website` repository.

1. Sign in to GitHub and open https://github.com/settings/personal-access-tokens/new
2. **Token name**: `TCC events admin`
3. **Expiration**: choose the longest option you are comfortable with (e.g. 1 year).
4. **Repository access**: *Only select repositories* → `tcc-website`.
5. **Permissions → Repository permissions → Contents**: **Read and write**.
6. Click **Generate token** and copy it (it starts with `github_pat_`).

Paste this token on the admin page and click **Sign in**. It is remembered only in
that browser. Anyone with the token can change the website, so keep it private.

## Using the admin page
- Fill in the form and click **Save event**. The website updates in about a minute.
- **Edit** loads an event back into the form; **Delete** asks you to click twice.
- Events whose date has passed move to "Past events" on the website automatically.

## Username and password sign-in

After the first-time setup, the admin page asks only for a username and password.

1. Open `admin.html`. If no admin account exists yet, it shows **First-time setup**.
2. Enter the username, a password, and the GitHub token (created as described above), then press **Save**.
3. The token is locked with the password and saved to `data/admin-key.json`. Nobody can read the token from that file without the password.
4. From then on, sign in with the username and password only. Use **Change password** after signing in to change either one.

If the token expires or is revoked, delete `data/admin-key.json` in GitHub and run the first-time setup again with a new token.
Use a long password: the locked file is public, so a short password could be guessed with enough computing power.

## Exam results (admin only)

On the admin page, **Exam results** saves marks for Grades 6–11 term tests, O/L and A/L.
Upload an Excel (.xlsx, .xls, .csv) or Word (.docx, marks in a table) file, check the detected columns in the preview, then **Save results**.
Results are encrypted with a key that is kept inside the password-locked sign-in file, so they can only be opened on the admin page after signing in. Use **View** to search and **Download Excel (CSV)** to export.
If the GitHub token changes, sign in and use **Change password** to enter the new token. Do not delete `data/admin-key.json`: that would make saved results impossible to open.
