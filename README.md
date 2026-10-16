# Unbox

**Scan a QR code on a box and see what's inside, before you open it.**

Unbox is a small web app for people who move house, hostel or city. Photograph the things you pack, put them in boxes (boxes can hold smaller boxes), print a QR label for each box, and anyone you share it with can scan the label, browse photos by box, or search for an item without unpacking a thing.

> Built out of a real interstate move: 79 items across 3 big boxes, catalogued with photos so the person receiving them can see what's inside before opening anything.

![Unbox on a phone: home, a box, an item, search](docs/screenshots/overview.png)

## What works today (prototype, v0)

- **Boxes inside boxes.** Big boxes hold small boxes and loose items. Counts and weights roll up.
- **Photo-first browsing**, designed for a phone: big tap targets, swipe between items.
- **Search by meaning.** Typing "something to drink from" finds the water bottle and the mug, and "wire for tv" finds the HDMI cable. It uses a small open-source embedding model (`bge-small`, via `fastembed`) that runs on the server's CPU: no paid API, no data sent to a third party.
- **QR labels** for every box, printable on A4.
- **Photo tools:** crop, rotate, and iPhone (HEIC) support.
- **Private by default:** every page and photo sits behind a password, and robots are told not to index it.

## Run it on your computer

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python seed_sample.py          # makes a made-up inventory in ./data (placeholder pictures)
EDIT_MODE=1 python app.py      # open http://127.0.0.1:5000
```

The first run downloads the search model (about 100 MB). `EDIT_MODE=1` turns on the editing screens, which only exist on your own machine. Without it the app is read-only and asks for a password (`APP_PASSWORD`).

## Roadmap

This is a single-owner prototype. The plan to make it usable by anyone is in [DESIGN.md](DESIGN.md):

- [ ] Accounts with email verification, many trips per user, archive and delete
- [ ] Hosted database and private photo storage (Supabase)
- [ ] **Batch capture:** take photos at your own pace, upload together, review all the suggested names on one screen, add weights
- [ ] **On-device AI naming**, so photos never leave the phone for the AI step; cloud AI only as an opt-in
- [ ] "Scan a pile": one photo, objects found and cropped automatically, unknown ones named "Unidentified item"
- [ ] A viewer password per trip, so the QR itself carries no secret
- [ ] Installable PWA, QR labels generated in-app

## Privacy

Photos from inside a home are sensitive. The design commitments (location data stripped from photos, private storage with short-lived links, real deletion, nothing sold or shared, AI on the device by default) are written down in [DESIGN.md](DESIGN.md).

## Tech

Python · Flask · SQLite (v0) · Pillow · fastembed · Docker · deployed on Render

## Licence

MIT. See [LICENSE](LICENSE).
