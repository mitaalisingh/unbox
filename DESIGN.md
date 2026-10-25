# Unbox: design decisions

Status: the v0 prototype exists (see README). This document records the decisions for the multi-user version and why they were made. It is also the plan for the report.

## The problem

After a move, people open every box to find one thing. The goal: scan a QR on a box and see its contents, with photos, organised the way the packer organised it, and search across everything.

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | **Installable web app (PWA)**, not a native app | The core moment is a tired person scanning a QR. A link works with no install and no app store. It is also an upgrade of the existing web app, not a rewrite, and avoids the yearly store fee. |
| 2 | **Supabase**: one Postgres database, plus auth and private file storage. **Flask backend kept.** | One free account covers users, trips, boxes, items, login and photos. Postgres has a vector extension, so semantic search can move into the database. The Python stack stays, so the AI pieces stay in Python. |
| 3 | **AI runs on our own server, not a third party's.** Photos are named by a small open-source model that runs inside the app's own server, so no photo goes to an AI company. Any cloud AI would be opt-in, off by default, per batch, with an explicit warning and a preview of what is sent (see "Naming items from photos" below). The user always confirms names. | Photos inside homes should not go to a third party without the user knowing. The first plan was to run the model on the user's phone, but the download is large and slow on cheap phones. Measurements showed the photo half of the model fits on the free server (see "What we built"). A small model names generic items well and specific products poorly, so the confirm step is part of the design. |
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

## Naming items from photos: local first, optional cloud boost

Decision: the default way to name a photo never sends it to another company. A cloud option may exist later, but only as an explicit, per-batch choice that is off by default.

### Why
Photos taken inside a home are sensitive, and many people do not know that "the AI names it for me" can mean "a company's computers looked at my photos". Accuracy matters, but not more than that.

### What we measured so far (local, free models)
Test set: 34 photos, each showing one real household item, scored against a general list of about 500 household names. An answer counted as right if it contained an acceptable word for that item. Scoring was lenient (for example "usb cable" for an HDMI cable counts, and nine of the 34 items are cables), so all numbers lean optimistic. With 34 photos, one photo is about 3 points, so small differences are noise.

| Model | Right answer first (full list / stricter list) | Right answer in top 3 (full / stricter) | Download |
|---|---|---|---|
| CLIP ViT-B/32 | 47% / 29% | 62% / 53% | about 0.6 GB |
| jina-clip-v1 | 41% / 41% | 65% / 65% | about 0.9 GB |
| **nomic-embed-vision-v1.5 + nomic-embed-text-v1.5** | **76% / 65%** | **79% / 74%** | about 0.9 GB |
| **SigLIP2 base (google/siglip2-base-patch16-224)** | **91% / 71%** | **94% / 79%** | image half about 0.37 GB; text half not checked |
| SmolVLM-256M (describes the item in words, no fixed list) | 32% | not applicable | about 0.5 GB, 3.5 s per photo on a laptop CPU |

"Stricter list" means the names I added while knowing the test items were removed, to estimate how a user with an unusual item would fare.

Findings:
- **nomic is clearly the best of the three** on this set. Generic, recognisable items (cables, extension boards, screws, tape, toys) are named well. Small or ambiguous items (a soft support, a small computer board, a display adapter, a cord reel) are missed.
- **Averaging models does not help.** Averaging all three scored lower than nomic alone, because it pulls the strong model toward the weak ones.
- **Prompt wording:** averaging three phrasings of each name helped CLIP (stricter top-3 from 53% to 65%) but made no difference for nomic. It is free, so we use it, but we do not count on it.
- **Confidence numbers are not reliable** (nomic's are tiny, 0.02 to 0.15; CLIP was about 0.5 sure of some wrong answers). Use the ranking (top 3), not a "sure enough?" cutoff.
- **Download size:** for a fixed name list, the text half of the model can be computed once and shipped as a small file (about 1.5 MB). The phone would then need only the image half (about 0.37 GB, with a compressed build listed at about 0.1 GB, untested).
- **Searching the photos directly works much better than naming them.** The model understands photos and text in the same space, so what the user types can be compared with the photos themselves, with no name needed. 29 searches, written by us:

| Model | Pool | First result right | Right result in top 3 | In top 5 |
|---|---|---|---|---|
| nomic | 34 photos | 72% | 90% | 100% |
| nomic | 78 photos | 66% | 90% | 90% |
| **SigLIP2** | 34 photos | 93% | 100% | 100% |
| **SigLIP2** | 78 photos (44 distractors) | **93%** | **100%** | **100%** |
| random guessing | 78 photos | n/a | 7% | n/a |

  Caveats: the searches were written after seeing the items and many are plain words ("hdmi", "raspberry pi"); real searches will be vaguer and score lower. 78 photos is still smaller than a real home.
- **Not yet measured:** speed and download size on a real phone; SmolVLM-500M; larger self-hosted models such as the Qwen or Gemma families; how to run the text half (needed to read what the user types) on a small server. This table will be updated.

### What we built (October 2026)
- **Only the photo half of the model runs on the server.** The model has a photo half and a text half. The text half is too big for a free 512 MB server, so it is not loaded at all. Instead the numbers for a fixed list of 512 everyday item names (`naming_data/`) were worked out once, ahead of time (`tools/build_labels.py`), and shipped as a small file. Naming a photo means comparing its numbers with that file.
- **Compressed:** the photo half is 4-bit (58 MB). On a Mac, the app, the typed-search model and the photo half together peaked at about 350 MB. This is not yet measured on the real server (Linux, shared CPU), so it must be re-checked after deploying.
- **Accuracy** on 34 photos of real household items, with a list that includes their names (so an optimistic test): first suggestion right 28 of 34 (82%), right name within the top three 31 of 34 (91%). With names the list was not written to match, results were lower (see the strict rows above). Names below a 10% confidence become "Unidentified item"; below 30% the review screen asks the person to check.
- **Speed:** about 0.1 to 0.2 seconds per photo on a laptop; slower on the free server's shared CPU.
- **Limits:** it can only suggest names from the list. Anything else is "Unidentified item" for the user to name. Photo search by typed words (comparing typed text with photo numbers) needs the text half and is **not built**; typed search still works on item names and tags.
- **Things tried and dropped:** running the AI in a separate free Hugging Face Space (Docker Spaces now need a paid plan); loading each half only when needed (memory was not given back, so it stayed near 500 MB); 4-bit and trimmed-dictionary variants of the text half (fit on a laptop, but still too large next to the app).

### Product design that follows
0. **Search the photos themselves (dropped for now: needs the text half of the model, see "What we built").** Each photo's image numbers are stored when it is uploaded, and a search compares what the user types with them. No item needs a correct name for it to be found. This is the feature that removes the need to review every name.
1. **Suggestions, not single guesses.** Show the top three names under each photo as tap buttons, plus "type your own". The right answer is among the top three 53 to 62% of the time, so most items cost one tap.
2. **"Unidentified item"** when nothing is close, with the photo kept so the user can name it.
3. **Learn from corrections.** When the user confirms a name, remember that photo's numbers and suggest the same name for similar photos (no extra download).

### How photos get in (decided)
**The phone's own camera app, then pick many photos at once.** Full quality, works today, needs no camera permission, and handles iPhone (HEIC) photos. The AI work starts after the photos are chosen, in small chunks, so taking photos is never slowed down by a model. A live in-page "burst" camera is not planned.

### Ideas parked (not planned now)
- Swipe-to-accept review queue ("swipe right to accept, down to mark as unsorted"). Cautions if it is ever built: the top guess is wrong often enough that fast swiping would lock in wrong names, so it would need the top three as tap buttons, an undo, a visible progress count, and button alternatives to swiping.
- A live in-page burst camera.

### The optional cloud boost (not built yet)
Only if local results stay weak. Rules:
- **Off by default, never pre-ticked, per batch.** A plain-language notice at the moment it happens, for example: "These 12 photos will be sent to [provider] to be named. They will not see your name or email." It says what is and is not sent and links the provider's terms. The time the user agreed is recorded. It is easy to switch off.
- **Show exactly what will be sent.** A preview of every cleaned, resized and cropped image, with a "don't send this one" switch per photo for anything sensitive (medicines, documents, parcels with addresses, people).
- **Minimise first.** Location and all hidden metadata are already removed on upload. Shrink the image (less detail is safer and cheaper) and crop to the item (manual crop first, automatic once the "scan a pile" feature exists).
- **Send through our server,** never from the browser, because a secret key must not be in a web page. No user id, names, tags, or trip names in the request. Do not store the photo or log it. Keep only the suggested name that comes back.
- **Choose the provider and plan deliberately.** Require terms that say API data is not used for training and is kept only briefly. Record which provider and plan, and re-check their current terms before launch. We have not verified any provider's terms yet.
- **Honest wording.** These steps reduce the risk; they do not make it zero. The app must never say "no risk". Reasons it is not zero: the item itself can be sensitive, the provider's own retention is outside our control, and the provider still sees requests arriving from our server.
- Also considered: running a larger model on a server we control (no third party sees the photo, but it costs money), and "bring your own key" for power users (cost and terms become theirs).

Before shipping any cloud option: confirm the provider's current terms and check with a mentor or lawyer how India's data protection law applies to sending users' photos to a third party. This document is not legal advice.

## Build order

0. Repo, branding, sample data, README, this document
1. Database layout, sign-up and login (Supabase)
2. Batch upload and review screen, with naming on our own server (done)
3. Trips, archive and delete, viewer password, in-app QR labels
4. Tap-to-crop, then "scan a pile"
5. Test with real users, measure the AI's accuracy, write the report

## How success will be measured

- **Search:** run a fixed set of test queries over a real inventory and report how often the right item is in the top 3, for keyword search versus meaning-based search.
- **Naming:** compare the model's suggested names against hand-written names for the same photos; report the share named correctly, and the share fixed by the user.
- **Users:** a small test with real movers; report what confused them.

## Where the build stands

- Done and tested against a real Supabase project: accounts with email confirmation and a consent record, many trips per user, archive and delete, and privacy between users (a second account sees none of the first account's trips).
- Not yet verified live: the password-reset email. Supabase's built-in sender is limited to a few emails per hour on the free plan.
- Done and tested: boxes inside boxes, items, private photo upload with location removal, and real file deletion (checked against the live storage bucket: nothing left behind).
- Done and tested: search inside a trip. The meaning-search database function was checked against the real project with the real model.
- Known limits: the search model is English only, and its cutoff is untuned (short item names with no tags give loose matches).
- Done and tested: editing (text, weight, photo replace and removal, moving between boxes), trip renaming, and the password-reset email.
- Done and tested: batch capture. Choose up to 20 photos, each becomes an item named by the AI (or "Unidentified item"), and one review screen lets the person fix names with one tap from the top three suggestions. 100 automated tests.
- Next: a custom email sender, deploying with an invite code, trying it on a real phone, then the viewer password and in-app QR labels.

## Known risks

- Free tiers change. Quotas and sleep behaviour must be re-checked at launch.
- **Email:** Supabase's built-in email sender is for testing only. Sources report about 2 emails per hour, delivery to spam, and silent failures once the cap is hit. Before real users, set up a custom SMTP provider (for example Resend, Brevo, SendGrid or Mailgun) with a verified sender, and confirm the current limits on the provider's own pages.
- The photo model's memory use is measured on a laptop only; on Render's free 512 MB it must be re-checked, and the first photo after a sleep is slow while the model loads.
- Phone screens can pause background work on iPhones; batches are processed in chunks and uploads must resume.
- Free storage (about 500 MB) fills quickly with photos; they are resized to keep that manageable.
