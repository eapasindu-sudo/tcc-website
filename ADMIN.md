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
