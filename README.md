# Breakout

Breakout for the terminal. Python 3 standard library only — no dependencies, no install.

```
python3 breakout.py
python3 breakout.py -l 4   # start at level 4 (1-10)
python3 breakout.py -c     # resume the session last saved with X
```

## Run it as `breakout`

`breakout.py` is executable and has a `#!/usr/bin/env python3` line, so you can
link it onto your `PATH` and start it from anywhere:

```
mkdir -p ~/.local/bin
ln -s "$PWD/breakout.py" ~/.local/bin/breakout   # run from the repo folder
breakout
```

If your shell cannot find `breakout`, `~/.local/bin` is not on your `PATH` yet.
Add this to `~/.bashrc` or `~/.zshrc` and open a new terminal:

```
export PATH="$HOME/.local/bin:$PATH"
```

It is a symlink rather than a copy, so a `git pull` in the repo updates the
command too. If you cloned with a tool that dropped the executable bit, run
`chmod +x breakout.py` once.

## Controls

| Key | Action |
| --- | --- |
| Left / Right or `A` / `D` | Move the paddle (`h` / `l` also works) |
| `Space` or Up / `W` | Launch the ball (`k` also works) |
| `P` | Pause |
| `R` | Restart |
| `X` | Save session and exit |
| `Q` or `Ctrl-C` | Quit |

As in Tetris, `Space` is not a pause key here: it launches the ball. Pause is
`P` only.

The ball starts each life resting on the paddle and travels with it until you
launch it, so you can pick where to serve from. Knock out every brick to clear
the wall. A ball that gets past the paddle costs a life.

## The paddle

Where the ball lands on the paddle sets its angle on the way back up:

| Part of paddle | Ball goes |
| --- | --- |
| Outer quarter | Out flat and fast, 2 columns per row, towards that side |
| Inner quarter | Up steeply, 1 column per row, towards that side |

So catching the ball near an end sends it skimming sideways, and the middle
sends it up into the wall. There is no straight-up bounce — a ball that only
went up and down could never be steered.

## Bricks

The wall is 6 rows of 12 bricks (`[==]`), coloured red at the top down to
blue at the bottom. There are two empty rows above it, so once you break a
hole through, the ball can get behind the wall and bounce around up there.

From level 2 the top row is armoured (`[##]`): it takes two hits, and shows
`[==]` after the first. Each level adds another armoured row, up to three.

## Lives

You start with 3 lives, up to a maximum of 5. Clearing every third wall gives
one back, shown as `+1 LIFE` beside the level-up banner. Miss the ball on your
last life and the run is over.

## Scoring

| Row (top first) | Points per brick |
| --- | --- |
| 1–2 | 7 × level |
| 3–4 | 5 × level |
| 5 | 3 × level |
| 6 | 1 × level |

Points are paid when a brick breaks, so an armoured brick scores once, on its
second hit. The far rows are worth the most because they are the hardest to
reach.

## Speed

Each wall you clear takes you up a level and builds a new one. The ball moves
one row every 80ms on level 1 and speeds up 6ms per level, down to a 35ms
floor from level 9. `-l` starts you higher, with the armour and speed of that
level.

Tuning constants live at the top of `breakout.py`: `BRICK_COLS`, `BRICK_ROWS`,
`HEIGHT`, `PADDLE_W`, `PADDLE_STEP`, `START_LIVES`, `MAX_LIVES`,
`EXTRA_LIFE_EVERY`, `START_TICK_MS`, `MIN_TICK_MS`, `SPEEDUP_MS`,
`ROW_POINTS` and `PADDLE_ZONES`.

## Saving a session

Press `X` to pause, park the current run to disk and exit. Resume it with:

```
python3 breakout.py -c     # or: breakout -c
```

The saved run comes back paused — the wall, the paddle, the ball's position
and direction, score, lives and level all pick up exactly where you left
them. Un-pause with `P` to keep going.

The save is consumed on resume: once `-c` has loaded it, the file is gone,
whether or not it was usable. That keeps an old save from being replayed after
a later game over, and running `-c` again with nothing saved just starts a
fresh game (reported in the side panel as "NO SAVED SESSION").

It lives at `$XDG_DATA_HOME/breakout/session`, defaulting to
`~/.local/share/breakout/session`. The wall is stored one string per row, `.`
for a broken brick and otherwise the hits it has left, so it is readable in a
text editor. Editing the size constants and resuming an old save, or resuming
a corrupted file, is rejected the same way: a fresh game rather than a crash.

If the write fails on `X` — read-only home, odd permissions — nothing is
lost: the game stays paused and open with a "SAVE FAILED" banner instead of
exiting.

## High score

The best score persists between sessions and shows under the current one.
Beating it shows `NEW BEST!` on the game-over banner.

It is stored at `$XDG_DATA_HOME/breakout/highscore`, defaulting to
`~/.local/share/breakout/highscore` — a plain text file, so `rm` it to reset.
Quitting or `Ctrl-C` mid-run still banks the score.

If the file cannot be written, the game keeps the record in memory for the
session and marks it `(unsaved)` rather than failing.

## Requirements

- Python 3 with the stdlib `curses` module. `curses` ships with Python on
  Linux and macOS, but **not** on native Windows — run it under WSL or install
  `windows-curses`.
- A terminal at least **70x24**. The game checks at startup and exits with a
  message rather than crashing if the window is too small.

## Layout

Game rules live in a `Game` class that never touches curses, so the logic can
be driven headlessly — a test can construct a `Game(rng=random.Random(seed))`,
call `move()`, `launch()` and `step()`, and assert on `bricks`, `ball_x` and
`ball_y` without a terminal. Rendering and input handling sit in `draw()` and
`run()`.

The invariant worth asserting after any step is that the ball is inside the
walls, above the paddle row, and not inside a brick (`game.brick_at(ball_x,
ball_y)` is `None`).

The ball moves on whole terminal cells: each tick it travels `|dx|` columns
one at a time, then one row. Moving one axis at a time means anything in its
way — wall, brick, paddle — is always met square on and bounced off by
flipping that axis, so there are no diagonal corner cases to get wrong.

A few details in `_loop()` are load-bearing and easy to undo by accident:

- Ball movement is driven by `time.monotonic()`, not by `getch()` returning.
  `getch()` returns as soon as a key arrives, so pacing the loop on input
  alone would speed the ball up whenever you hold an arrow key.
- `curses` `timeout()` overrides `nodelay()`, so the input drain loop re-arms
  `nodelay` on every pass. Without it a steady stream of keypresses never lets
  the buffer empty and the game stops advancing.
- The tick is rescheduled on launch, so a freshly served ball gets a full tick
  before it first moves rather than whatever was left over.

`Game.to_dict()` / `Game.load_dict()` round-trip the whole run through plain
JSON for the `-c` / `X` session save. `load_dict()` validates into local
variables before touching `self`, and rejects a save whose ball sits inside a
brick, so a hand-edited file can't leave a half-restored game.
