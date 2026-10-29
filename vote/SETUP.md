# Online election: one-time setup (Supabase, free)

1. Go to https://supabase.com, sign in with the school's Google/GitHub account and press **New project**.
   Name: `tcc-election`, choose a database password (keep it safe), Region: **Singapore** (closest to Sri Lanka). Wait about a minute.
2. Left menu → **SQL Editor** → **New query**. Paste everything from `vote/setup.sql` and press **Run**.
3. Left menu → **Authentication** → **Users** → **Add user** → **Create new user**. Enter the admin email and a strong password, tick **Auto Confirm User**.
4. Back in **SQL Editor**, run this one line (with that email):
   `insert into el_admins (user_id) select id from auth.users where email = 'admin-email@gmail.com';`
5. **Authentication** → **Sign In / Providers** → turn **off** "Allow new users to sign up".
6. **Project Settings** → **API**: copy the **Project URL** and the **anon public** key and send them to the web team (these two are safe to share; the database rules protect the data).
   They go into `vote/config.js`.

Then:
- Admin panel: https://eapasindu-sudo.github.io/tcc-website/vote/admin.html (sign in with the email and password from step 3)
- Students vote at: https://eapasindu-sudo.github.io/tcc-website/vote/

Free plan note: Supabase pauses a free project after a week without use. Open the Supabase dashboard and press **Restore** before the election if that happens.
