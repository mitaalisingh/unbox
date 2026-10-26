# Unbox

**Photograph what you pack. Unbox names it. Scan a QR on the box to see what's inside, before you open it.**

Unbox is a small web app for people who move house, hostel or city. Take photos of your things, put them in boxes (boxes can hold smaller boxes), print a QR label for each box, and share a trip with family or friends so they can browse it without unpacking anything.

> Built out of a real interstate move (Indore to Bangalore): 79 items across 3 big boxes, catalogued with photos so the person receiving them could see what was inside.

**Live:** https://unbox-aw54.onrender.com (free hosting: the first visit after a quiet spell can take about a minute to wake up)

![Unbox on a phone: intro, login, trips, a trip](docs/screenshots/overview.png)

## What it does

- **Accounts and trips.** Sign up with email (18+ and privacy notice consent are recorded), then keep many trips: create, archive, restore, delete. Every trip gets an illustration; you can pick another or upload your own photo.
- **Add items fast.** Choose up to 20 photos at once. Each is named automatically by a small open-source image model running on our own server, and one review screen lets you fix the few it got wrong. Unknown things become "Unidentified item". Photos are never sent to another company.
- **Boxes inside boxes.** Loose items and sub storages (box, bag, drawer). Counts and weights add up. View items as a grid or a swipe view with a Details card (weight, tags, note). Press and hold an item and drop it on a box to move it.
- **Search inside a trip** by words, typos, or meaning ("something to drink from" finds the mug), using a small open-source text model.
- **Share view-only by QR.** Turn sharing on with a password or none; print a QR for the whole trip and one per box. Passwords are stored hashed, wrong guesses are rate limited, changing the password ends every old viewer pass.
- **Trip codes.** A one-time code (valid 7 days, stored only scrambled) lets a friend copy the whole trip, photos included, into their own account.
- **Lock a trip** once everything is packed: view-only for the owner too, enforced by the server.
- **Private photos.** GPS and all other metadata are stripped on upload; photos live in a private bucket and are shown through links that expire after an hour.
- **Delete my account** removes the account with all its trips, items and photos.
- **Installable** on a phone's home screen; a plain-language privacy notice is built in.

## How it's built

Python · Flask · Supabase (Postgres with row-level security and pgvector, Auth, Storage) · SigLIP2 image model (ONNX, 4-bit) for naming · `bge-small` (fastembed) for search · Pillow · pytest · Docker · Render

- The database's own row-level rules stop one account from reading another's data. Calls are made with the logged-in user's token; the server's secret key is used only for the viewer and delete-account flows, after the checks.
- Safety basics: form tokens against forged requests, HttpOnly cookies, no open redirects, security headers.
- The naming model was chosen to fit a free 512 MB server: about 8 in 10 first suggestions right and 9 in 10 within the top three on a 34-photo test set. Measurements and trade-offs are in [DESIGN.md](DESIGN.md).
- 170+ automated tests use an in-memory stand-in for Supabase and cover bad input, wrong passwords, forged links, photo cleaning (including a photo that carries GPS) and one user reaching for another's data.

## Run it yourself

You need your own free Supabase project: apply `supabase/schema.sql` in its SQL editor, copy `.env.example` to `.env` and fill in your keys (never commit `.env`), then:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
DEV_HTTP=1 PORT=5050 python webapp.py     # open http://127.0.0.1:5050
```

Tests:

```bash
pip install -r requirements-dev.txt
pytest
```

The first run downloads the models. Supabase's built-in email sender is limited to a few messages an hour on the free plan, so a public launch would need an email provider of its own.

`app.py` is the original single-owner prototype (boxes, photos, search and QR labels on your own computer); `webapp.py` is the multi-user app above.

## Roadmap

- [ ] Own email provider (SMTP) before opening to many users
- [ ] "Scan a pile": one photo, objects found and cropped automatically
- [ ] Measure search quality on a real inventory and tune it
- [ ] A multilingual search model (the current one is English only)

## Privacy

Photos from inside a home are sensitive. The commitments are written in [DESIGN.md](DESIGN.md), and the app ships a plain-language privacy notice (Profile, the login screens and the sign-up tick box link to it). It has not been reviewed by a lawyer.

## Licence

MIT, covering this code. See [LICENSE](LICENSE). The AI models it downloads have their own licences.
