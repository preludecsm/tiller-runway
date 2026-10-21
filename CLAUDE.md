# tiller-runway

A **public** demo: retirement budgeting built on a Tiller Money Feeds Google Sheet, published as
five static pages plus an aggregate-only advisor PDF. Live at
`preludecsm.github.io/tiller-runway`, served from `docs/` by GitHub Pages.

**Everything here is synthetic.** The household is "Alex & Jordan", every figure is fabricated by
`scripts/generate_demo_data.py`, which writes them into a public sample sheet the renderers read. There is nothing real to
protect in this repo — which is exactly why the rules below are about keeping it that way.

## The three things that would actually cause harm

**1. Publishing a real figure.** `docs/` is a live public website, not a build directory. Scripts
write to `reports/` (gitignored); copying into `docs/` is the publish step, and anything there is
on the open internet at the next push. Every number in it must come from the
generator or the demo sheet. If a figure cannot be traced to one of those, it does not go in.

**2. Holding a credential for real data.** Every script loads a service-account key from
`./*.json`, so the repo needs one to run at all, and `generate_demo_data.py` and
`style_demo_sheet.py` need Editor access on the demo sheet. That key must be authorised on the
demo sheet **and nothing else**, ideally through its own service account, not a second key on
the one the private repo uses. A key here once opened a real household's sheet too; that was
removed (verified 2026-10-20). The rule: **if this key can open the real household's sheet, it's
the wrong key.** `*.json` is gitignored, which is a backstop and not a permission. The right key
belongs to `runway-demo@tiller-runway-demo.iam.gserviceaccount.com` (GCP project
`tiller-runway-demo`, created 2026-10-20 for this repo alone, Sheets API only), shared
as Editor on the demo sheet only. A key for any other account is wrong.

**3. Reaching into the private system.** A separate, private repo runs the real household this
demo is modelled on. On this host it is adjacent on disk at `/opt/tiller/repo`. **Do not read it,
copy from it, or reason about it here.** No subsumption in either direction and no shared data or
code paths — that boundary is deliberate and predates this file. If something here would be better
informed by what that project knows, the answer is to work there, not to look across.

## What this repo is, concretely

| | |
|---|---|
| Pages | `index.html` landing page, plus `dashboard`, `spending`, `tiers`, `downsize`, `sustainability`, each from `scripts/render_<name>.py` |
| PDF | `render_advisor_pdf.py` — **rollups only**, no individual transaction, safe to hand to a third party |
| Data | `generate_demo_data.py` builds the synthetic household; the sheet holds every assumption |
| Config | `config.py` / `config_local.py` — the sheet id and local overrides live here, never hardcoded |
| Output | `reports/` (gitignored scratch); `docs/` is the published copy. Treat a change there as a publish |

**Assumptions live in the sheet or `config.py`, not in renderers.** That is the demo's main
argument: change a number, re-run, done. A hardcoded rate in a renderer is a bug even when it
produces the right figure.

**There are no dedicated Travel or Subscriptions reports, on purpose.** They need hand-curation
and don't generalise to a stranger's data, so don't add them. The Travel and Subscriptions
*categories* exist in the demo data and must stay.

## Housekeeping

Own remote (`git@github.com-tillerrunway:preludecsm/tiller-runway.git`) and own deploy key. On
this host the checkout is `/opt/tiller/runway`, in tmux window `runway` of session `tiller`.
Claude Code keys its history to the working directory, so a session started anywhere else gets a
different project's context — start in this directory or not at all.

Not this repo's scope: tmux, the host, systemd timers and anything about the AWS box, all of which
belong to the infra project.
