# TCC Student Parliament Election

1. Put `TCC-Election.exe` in a folder on the main computer and double-click it. The voting screen opens full-screen (press Alt+F4 to close it).
2. Open the admin panel in a browser on the same computer: http://localhost:8765/admin
   The first time, choose an admin password.
3. Admin panel: add candidates (number, name, class, photo), upload the students' Excel file (name, class, admission/index number), then press **Open voting**.
4. Students type their admission/index number, check their name and class, click their candidate's photo (or type the candidate number) and confirm.
5. Results update live in the admin panel. The app records that a student voted, but not whom they voted for.

More voting computers: on the same school network, open the address shown in the admin panel (for example http://192.168.1.20:8765) in a browser. The first time, Windows may ask to allow the program through the firewall: allow it on private networks.

All data is kept in the `TCC-Election-Data` folder next to the program. Copy that folder to back it up.
Run `TCC-Election.exe --window` to open the voting screen in a normal window instead of full-screen.
