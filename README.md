# 0/lethe

A machine that reads the shadows of lost things and brings them back.

Nothing disappears cleanly. Everything that existed left traces in the things
that survived it — a dead page in the links still pointing at it, a lost book in
the quotes scattered through surviving ones, a demolished building in a
photograph and a pair of coordinates. This machine reads those shadows and
rebuilds what cast them.

It never invents. Every fact it asserts is labelled **recovered**, **inferred**,
or **missing**, carries a calibrated confidence, and is traceable back to the
real evidence it came from. A machine that makes things up is worthless; one that
rebuilds the truth from fragments is the whole point.

Everything it runs on is free: open archives, open data, open source, your own
machine. No key, no account, no cost.

## What it does

- **Rebuilds** lost web pages, lost ancient texts, and demolished places — each
  from its own kind of surviving trace.
- **Discovers** what nobody catalogued as lost: by cross-referencing everything
  it has recovered, it finds the voids the most independent sources still point
  at — and rebuilds pages that have *no surviving copy* from the testimony of
  everything that linked to them.
- **Foresees** loss: it scores living things by how close they are to vanishing
  without a trace, and preserves the most endangered before they go.
- **Moves through time** — from antiquity to this morning — and real geography.

It runs itself. Pointed at nothing in particular, it seeds from the whole of
recorded knowledge, follows the trails it uncovers, and gets stronger the longer
it runs. Nothing is hand-listed.

## Run it

Python 3 standard library only. Zero dependencies.

```
python -m lethe.cli run                 # the machine runs itself, across everything
python web/server.py                    # then open the field at 127.0.0.1:1860
```

Point it by hand, if you like:

```
python -m lethe.cli case    <url>               rebuild one lost page
python -m lethe.cli hunt    "<wikipedia title>"  work the ghosts an article cites
python -m lethe.cli geo     [neighborhoods...]   recover lost personal homepages
python -m lethe.cli text    [author [year]]      rebuild a lost work from quotations
python -m lethe.cli places  [n]                  stand up demolished buildings
python -m lethe.cli foresee "<wikipedia title>"  find & preserve the endangered
python -m lethe.cli ghosts                        the most-referenced missing things
python -m lethe.cli rebuild <url>                 rebuild a copy-less page from shadows
python -m lethe.cli show    <id>                  a reconstruction and its full trail
python -m lethe.cli export  [dir]                 freeze the field into a static site
```

The operator uses the commands. Everyone else sees only the field: darkness, and
the faint light of what has been brought back. You move through it — drag across
space, scrub the scratch at the bottom through time, follow a thread from one
lost thing to the next.

## The field, in the world

`export` freezes the whole field into a self-contained folder — the same
dark front-end plus the graph as static JSON, no server or database. Drop it on
any static host; the machine keeps running and `export` refreshes the snapshot.

## Shape

```
lethe/
  db.py            the evidence graph — the spine; the law lives in the schema
  http_util.py     the only door outward; robots-respecting, rate-limited
  detect.py        is this thing a ghost? (does it answer nothing now?)
  extract.py       reading content, images and link-context out of a capture
  fingerprint.py   compact signatures, so a fragment can be recognised later
  reconstruct.py   assemble & score — every claim labelled and sourced
  foresee.py       fragility, and preservation before loss
  discover.py      convergence — the voids the world keeps pointing at
  reassemble.py    rebuilding a copy-less page from inbound testimony
  case.py          the detective loop, and the per-medium reconstructors
  engine.py        the machine running itself: a self-seeding frontier
  api.py           the one shape of the field, read from the graph
  export_static.py freezing the field for the open web
  sources/         where shadows come from: wayback, wikipedia, wikiquote, wikidata
  cli.py           the way in, for the operator
web/
  server.py        a local threshold between the graph and the dark
  field.js         the field itself — soot, and light scratched out of it
```
