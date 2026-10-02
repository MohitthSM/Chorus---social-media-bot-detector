# Labeling policy

Chorus puts labels next to real people's names. These rules keep that fair and defensible. Contributions that break them will not be merged.

## 1. Describe behaviour, never identity or motive

Allowed: "Coordinated activity", "Copy-paste talking points", "Frequent abusive content".

Not allowed in labels, summaries or UI: "bot", "troll", "paid", "IT cell", "propagandist", "agent", or any party or ideology name. Chorus cannot observe who runs an account or who pays for it, so it must not say.

## 2. Every label shows its evidence

A label must come with the counts that produced it and links to example posts. If a new signal cannot be explained in one sentence to the person it labels, it does not become a label.

## 3. No single signal is enough

Coordination labels need at least two independent kinds of evidence. The scoring weights are set so that timing alone, reposts alone or handle shape alone cannot reach the threshold. Clusters that target a single account also need near-identical text, because fandoms look coordinated on timing.

## 4. Viewpoint-neutral by construction

Features describe behaviour: timing, duplication, repost overlap, handle shape, abuse rate. No feature encodes political stance, party, religion, or which accounts are being supported or attacked. When evaluating changes, test on networks from across the spectrum. A detector that only ever fires on one side is broken, even if each individual flag looks right.

The abuse classifier is the one place where content matters. Test it on benign political criticism in every supported language before changing thresholds. Ordinary criticism of any government or party is not abuse.

## 5. Prefer missing a ring to flagging a person

Thresholds are set for precision. A false label on a real person does more harm than a missed ring. Known misses are documented in the README instead of being "fixed" with looser thresholds.

## 6. No hand-made lists of named accounts in this repo

Do not commit seed lists, blocklists or "known propagandist" files naming real accounts. If a deployment wants seed data, it must keep that data outside the repo, and each entry needs a published source, such as a platform takedown report or a court record, that the operator can point to.

## 7. Appeals are part of the product

"Report a mistake" must stay visible on every evidence panel. An operator running a shared backend must review appeals (`/v1/admin/appeals`) and can suppress labels (`/v1/admin/overrides`). A suppressed account shows no label, and that suppression persists across re-analysis.

## 8. Data minimisation

The extension sends only public posts that are on the user's screen, and only while sharing is switched on. The backend keeps observations for `retention_days` (default 30) and then deletes them. Do not add collection of private data, DMs, follower lists fetched in the background, or anything the user did not already see.
