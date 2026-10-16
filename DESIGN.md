# Unbox: design decisions

Status: the v0 prototype exists (see README). This document records the decisions for the multi-user version and why they were made. It is also the plan for the report.

## The problem

After a move, people open every box to find one thing. The goal: scan a QR on a box and see its contents, with photos, organised the way the packer organised it, and search across everything.

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | **Installable web app (PWA)**, not a native app | The core moment is a tired person scanning a QR. A link works with no install and no app store. It is also an upgrade of the existing web app, not a rewrite, and avoids the yearly store fee. |
| 2 | **Supabase**: one Postgres database, plus auth and private file storage. **Flask backend kept.** | One free account covers users, trips, boxes, items, login and photos. Postgres has a vector extension, so semantic search can move into the database. The Python stack stays, so the AI pieces stay in Python. |
| 3 | **AI runs on the user's phone by default.** Cloud AI is opt-in, off by default, with an explicit warning. The user always confirms names. | Photos inside homes should not go to a third party without the user knowing. A small on-device model names generic items well and specific products poorly, so the confirm step is part of the design. |
| 4 | **Batch flow.** Take photos at your own pace, upload together, the AI names the whole batch, one review screen with a weight field per item. Later: tap-to-crop, then "scan a pile" with automatic cropping. | Reviewing one photo at a time is frustrating while packing. Items wrapped in bubble wrap cannot be named by any AI, so photos must be taken before wrapping. Automatic pile detection is the hardest part and is a stretch goal. |
| 5 | **Accounts with email verification** and reset by email. Many trips per user, with archive and delete. **A viewer password per trip**; the QR contains no secret. Roles: owner and viewer (collaborator later). | The person receiving the boxes should not need an account. A QR with a secret in it would show the contents to anyone who scans it, including a delivery person. The owner can change the viewer password at any time. |
| 6 | **Privacy rules** (below) | Photos taken inside homes are sensitive, and India's data protection law applies to anyone who runs this for others. |
| 7 | **Render** (app) and **Supabase** (data) for the demo | Render's free web service needs no card; sleeping after 15 minutes idle is acceptable for a demo. State lives in Supabase, so restarts lose nothing. |

## Privacy commitments

- Strip location (EXIF GPS) from every photo on upload.
- Resize photos on upload; do not keep full-size originals.
- Private storage; photos are shown through short-lived links.
- Row-level rules in the database: users can only read their own trips.
- Delete means delete: photo, item, trip, or whole account. Archive hides a trip but keeps it, and is clearly labelled as different from delete.
- No ad trackers, no selling or sharing of data. Anonymous counts only.
- 18 and over only; a plain privacy page; a breach plan written before launch.
- The developer can technically see stored data in the database dashboard. The rule: do not look, except for a support request. End-to-end encryption would prevent it but would break server-side search, so it is listed as future work.

Before opening this to the public: check the current status of India's Digital Personal Data Protection Act and its Rules with a mentor or lawyer. This document is not legal advice.

## Build order

0. Repo, branding, sample data, README, this document
1. Database layout, sign-up and login (Supabase)
2. Batch upload and review screen, with on-device naming
3. Trips, archive and delete, viewer password, in-app QR labels
4. Tap-to-crop, then "scan a pile"
5. Test with real users, measure the AI's accuracy, write the report

## How success will be measured

- **Search:** run a fixed set of test queries over a real inventory and report how often the right item is in the top 3, for keyword search versus meaning-based search.
- **Naming:** compare the on-device model's suggested names against hand-written names for the same photos; report the share named correctly, and the share fixed by the user.
- **Users:** a small test with real movers; report what confused them.

## Known risks

- Free tiers change. Quotas and sleep behaviour must be re-checked at launch.
- On-device models are slow on cheap phones and need a first-time download.
- Phone screens can pause background work on iPhones; batches are processed in chunks and uploads must resume.
- Free storage (about 500 MB) fills quickly with photos; they are resized to keep that manageable.
