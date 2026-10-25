# Settings workflow

This page covers the `section.key` view (`config-*`). Model connections, model assignment, the
embedding source and rerank connection are node settings: use `providers`, `settings-show` and
`settings-set` from [setup.md](setup.md) for those. Both views share the same preview / `--apply`
discipline below.

## 1. Inspect before proposing

List the runtime-writable keys and their consequences:

```
KA config-options
```

Read the current effective value, optionally for one section:

```
KA config-show --section SECTION
```

Secret-shaped keys are redacted even if the API already masks them.

## 2. Preview the exact mutation

Convert the user's natural-language intent to one writable `SECTION KEY VALUE` tuple. Run without
`--apply` first:

```
KA config-set SECTION KEY VALUE
```

Show the returned current value, proposed value, and consequence:

- `none`: saved without restart or rebuild.
- `restart`: saved, then a separately confirmed service restart is needed.
- `rebuild`: saved, but correct activation also needs a separately confirmed restart and an index
  rebuild. Section 4 covers that rebuild; never start it as a silent side effect of the config change.

Ask for explicit confirmation of the exact diff. A vague earlier request to "configure it" is not
confirmation of a newly discovered value or consequence.

## 3. Apply only after confirmation

After confirmation, repeat the same command with `--apply`. Do not combine multiple unreviewed changes.

```
KA config-set SECTION KEY VALUE --apply
```

Report the API result. If a restart is required, preview it first:

```
KA restart
```

Explain that Knowledge Arch briefly stops answering while the service restarts. Only after separate confirmation run:

```
KA restart --apply
```

## 4. Index rebuild

`index-rebuild --apply` is the programmatic equivalent of the "Rebuild index" button in the app. It is a
mutation: run it only when the user asks for a rebuild, never on your own initiative.

Check the state first — it is read-only and often makes the rebuild unnecessary:

```
KA index-status
```

- `pending_reindex_count: 0` and `compatible: true`: nothing to rebuild. Say so instead of rebuilding.
- `active_job` is not null: a rebuild is already running. Report its `progress_percent` and wait;
  starting another one does nothing, because the server is single-flight and returns the same job.
- `auto_rebuild_enabled: true` (default since v1.1.2): Knowledge Arch drains the queue by itself after a
  short debounce. Documents that just arrived are usually already handled — say that before offering a
  manual rebuild, and prefer waiting one debounce window over rebuilding immediately.
- `auto_rebuild_enabled: false`: a manual rebuild is the only way the queue gets drained. Offer either
  the rebuild or `config-set vector_db auto_rebuild_enabled true`, which needs no restart.

Preview before applying:

```
KA index-rebuild
```

Report `pending_reindex_count` and the effect: every pending document is re-embedded, and when the
index is incompatible the server clears the whole vector collection first, so retrieval stays degraded
until the rebuild finishes. Ask for explicit confirmation, then:

```
KA index-rebuild --apply --wait
```

Without `--wait` the command returns as soon as the background job starts; poll `index-status` for
progress. With `--wait` it polls until a terminal status. A `status: "timeout"` result means the client
stopped observing — the rebuild is still running. Report that honestly and keep polling `index-status`;
never describe it as cancelled and never start a second rebuild.

A `final_status` of `partial_failure` means some documents failed and stayed queued. Report the failed
count and the first error rather than claiming success.

Never bypass a rejected key by editing Knowledge Arch files or storage. Never read, expose or write
API keys or tokens yourself; when one is needed, use `request-secret` (see setup.md) so the user types
it into the app. Do not modify storage paths marked structural or any key absent from `config-options`.
