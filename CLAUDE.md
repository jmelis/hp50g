# HP 50g over USB

`./hp` drives the user's real HP 50g from this Mac. Use it instead of
re-investigating the protocol. `HP 50g AUR.pdf` (Advanced User's Reference)
is the local manual — check it before guessing about calculator behavior.

## Answering "how do I … on the calc?" questions

Base the answer on `commands.json`, never on memory. It is built from the
AUR by `make build` (`build.py` merges `data/menus.json` + `data/entries.json`).

- `commands.<NAME>` — one entry per command: `summary`, `stack` (stack
  diagrams, e.g. `n m → Cn;m`), `locations` (menu path, e.g.
  `MTH NXT PROB`), `menu_numbers` (look up in `menu_legend`), `flags`,
  `notes`, `see_also`, `categories`, `pages` (AUR pages).
- `topics` / `topic_blurbs` — task-oriented overviews (e.g. Probability →
  "Distributions"). Check these too: they say what is *not* built in
  (e.g. binomial/Poisson distributions need a short program, while the
  binomial coefficient is `COMB`).

Search with `jq` or Python over summaries, names and blurbs. In the answer,
name the command(s), give the stack diagram and keystroke/menu location from
the entry, and say it came from `commands.json`. Only fall back to the AUR
PDF for details the entry lacks, and say so. If nothing in `commands.json`
covers it, say that rather than guessing. Optionally confirm on the device
with `./hp eval`.

## Usage

Prerequisite: on the calc, `XSERV` ENTER (XModem server). If the calc shows
an error instead, ask the user to press ON and run `XSERV` again.

```
./hp eval "LINE"     # run LINE, print what it pushed; calc stack unchanged
./hp exec "LINE"     # run LINE, leave results on the calc stack
./hp stack | ls | mem | screen | get NAME [-o FILE]
./hp                 # interactive REPL for the user (shows stack each line)
```

`eval` is the right tool for "write a program and run it": define it with
`« ... » 'NAME' STO` in one eval, run it in another, `'NAME' PURGE` after.
Results come back as the calc's `→STR` text (strings quoted). Errors exit
non-zero with the calc's message (`Error: Too Few Arguments`).

Avoid built-in command names for variables (`'SQ'` = Invalid Syntax).
Unicode `« » → √ π ≤` or trigraphs `\<< \>> \-> \v/ \pi` both work.

Only one `hp` process may talk to the calc at a time: it takes an exclusive
`flock` on `~/.hp50g.lock` for its whole life (a REPL holds it until quit).
A second one exits with "in use by pid …"; pass `--wait` to queue instead.

Speed: ~1–2 s per command round trip, ~3.5 s for `screen`. `hp50g.py`
(Python + pyusb + Homebrew libusb, venv in `.venv`, auto-created by `./hp`).

## Hard-won protocol facts (all verified on the device)

- USB `03f0:0121` "HPx9G+ Device", vendor-specific, bulk IN `0x81` /
  OUT `0x03`. No driver/serial device on macOS; libusb works without root.
- Server packets: 2-byte big-endian length, data, 1-byte sum-mod-256
  checksum. Must ACK (`0x06`) or the calc resends forever.
- `M` → free memory, `L` → directory listing (len, name, 2-byte prolog,
  3-byte size, 2-byte crc per entry).
- `E` + framed command line → ACK. Must be sent in the **same write** as
  `E`; if delayed, the calc NAKs 10× and then reads the bytes as commands.
- `G` + framed name → ACK, then calc is an XModem sender (checksum mode).
  Repeat NAK until it starts. Data: `HPHP49-?` header + object nibbles.
- After `E` or `G`, the calc is busy for a moment and **drops** commands —
  ping with `M` until it answers (`Calc.sync`).
- **Any calc error kills XSERV** — e.g. `G` of a missing variable, or an
  `E` line that errors. That's why `Calc.run` wraps every line in
  `IFERR "line" STR→` and `Calc.get` checks `L` first. Never call
  `_execute_raw` / `_get_raw` with something that can fail.
- `KEYEVAL ab.c` (simulated keypress) and `LCD→` (screen grab) exist, but
  while XSERV runs the screen shows XSERV status and the calc's UI loop is
  blocked, so remote UI control is not practical.
- Not implemented: `P` (put file). Programs are sent as source via `eval`
  instead.
