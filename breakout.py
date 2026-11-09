#!/usr/bin/env python3
"""Breakout for the terminal. Stdlib only.

Run:  python3 breakout.py      (add -c to resume a saved session)
Keys: Left/Right or A/D move the paddle, Space or Up launch the ball,
      P pause, R restart, X save & exit, Q quit.
"""

import argparse
import curses
import json
import os
import random
import sys
import time

# Playfield in terminal columns and rows. Unlike Snake and Tetris the ball
# moves one column at a time, so the field is not doubled up into square
# cells; the paddle zones below make up for the 2:1 aspect instead.
BRICK_W = 4                # columns per brick, drawn as "[==]"
BRICK_COLS = 12
WIDTH = BRICK_W * BRICK_COLS
HEIGHT = 20
BRICK_TOP = 2              # empty rows above the wall, so the ball can get behind it
BRICK_ROWS = 6
PADDLE_W = 8
PADDLE_STEP = 3            # columns a single tap moves the paddle
# Holding a key glides the paddle at this speed instead of hopping once per
# key-repeat event. Curses only sees key presses, never releases, so a hold
# is inferred from repeats arriving: see Glide.
PADDLE_SPEED = 60          # columns per second while a key is held
GLIDE_FRAME_MS = 10        # how often a gliding paddle is redrawn
MAX_REPEAT_GAP_MS = 150    # a longer silence than this means the key is up

START_LIVES = 3
MAX_LIVES = 5
EXTRA_LIFE_EVERY = 3       # clearing every third wall gives a life back
START_TICK_MS = 80         # ball speed at level 1: one row per tick
MIN_TICK_MS = 35
SPEEDUP_MS = 6             # shaved off the tick per level
MAX_START_LEVEL = 10
MAX_TOUGH_ROWS = 3         # rows of two-hit bricks at the top, from level 2
EFFECT_FLASH_MS = 1500     # how long a "LEVEL 2!" style banner stays up

# Points per brick by row, top first, multiplied by the level. The far rows
# are worth the most, as they are the hardest to reach.
ROW_POINTS = [7, 7, 5, 5, 3, 1]

# Horizontal speed off the paddle, by where on it the ball lands, left to
# right. The ends send it out flat and fast, the middle steeply. There is no
# zero: a ball going straight up and down could never be steered.
PADDLE_ZONES = [-2, -2, -1, -1, 1, 1, 2, 2]

SAVE_VERSION = 1           # bump when the saved-session format changes

assert len(ROW_POINTS) == BRICK_ROWS
assert len(PADDLE_ZONES) == PADDLE_W


class Game:
    """Wall, paddle and ball, and the rules. Knows nothing about curses."""

    def __init__(self, width=WIDTH, height=HEIGHT, rng=None, start_level=1):
        if width % BRICK_W:
            raise ValueError("width must be a whole number of bricks")
        self.width = width
        self.height = height
        self.cols = width // BRICK_W
        self.rng = rng or random.Random()
        self.start_level = start_level
        self.reset()

    def reset(self):
        self.score = 0
        self.lives = START_LIVES
        self.level = self.start_level
        self.alive = True
        self.paused = False
        self.ticks = 0
        self.elapsed_ms = 0       # game time, advanced one tick at a time
        self.effect = ""          # banner text after a lost ball or level-up
        self.effect_until_ms = 0
        self.paddle_x = (self.width - PADDLE_W) // 2
        self._build_wall()
        self._serve()

    def _build_wall(self):
        """A fresh wall. bricks[row][col] is the hits a brick has left."""
        tough = min(MAX_TOUGH_ROWS, self.level - 1)
        self.bricks = [
            [2 if row < tough else 1] * self.cols for row in range(BRICK_ROWS)
        ]

    def _serve(self):
        """Put the ball back on the paddle, waiting to be launched."""
        self.launched = False
        self.dx, self.dy = 1, -1
        self._carry_ball()

    def _carry_ball(self):
        self.ball_x = self.paddle_x + PADDLE_W // 2
        self.ball_y = self.paddle_row - 1

    @property
    def paddle_row(self):
        return self.height - 1

    @property
    def tick_ms(self):
        ms = START_TICK_MS - (self.level - 1) * SPEEDUP_MS
        return max(MIN_TICK_MS, ms)

    @property
    def delay_ms(self):
        """How long until the next step() is due. Kept as a separate name
        from tick_ms to match the other games' loops."""
        return self.tick_ms

    @property
    def active(self):
        return self.alive and not self.paused

    @property
    def bricks_left(self):
        return sum(1 for row in self.bricks for hits in row if hits)

    def brick_at(self, x, y):
        """(row, col) of the brick covering column x, row y, or None."""
        row = y - BRICK_TOP
        if 0 <= row < BRICK_ROWS and 0 <= x < self.width:
            col = x // BRICK_W
            if self.bricks[row][col]:
                return row, col
        return None

    def move(self, dx):
        """Slide the paddle. A ball still waiting on it comes along."""
        if not self.active:
            return False
        x = max(0, min(self.width - PADDLE_W, self.paddle_x + dx))
        if x == self.paddle_x:
            return False
        self.paddle_x = x
        if not self.launched:
            self._carry_ball()
        return True

    def launch(self):
        """Send a waiting ball off the paddle, up and to a random side."""
        if not self.active or self.launched:
            return False
        self.launched = True
        self.dx = self.rng.choice([-1, 1])
        self.dy = -1
        return True

    def _set_effect(self, text):
        self.effect = text
        self.effect_until_ms = self.elapsed_ms + EFFECT_FLASH_MS

    def _hit(self, row, col):
        self.bricks[row][col] -= 1
        if not self.bricks[row][col]:
            self.score += ROW_POINTS[row] * self.level

    def _move_ball(self):
        """One tick of ball travel: |dx| columns, then one row.

        Moving one axis at a time means a brick or wall in the way is always
        hit square on, from the side or from above or below, and the ball
        bounces off by flipping that axis. There is no corner case to get
        wrong because there are no diagonal steps.
        """
        step = 1 if self.dx > 0 else -1
        for _ in range(abs(self.dx)):
            nx = self.ball_x + step
            if not 0 <= nx < self.width:
                step = -step
                continue
            brick = self.brick_at(nx, self.ball_y)
            if brick:
                self._hit(*brick)
                step = -step
                continue
            self.ball_x = nx
        self.dx = step * abs(self.dx)

        ny = self.ball_y + self.dy
        if ny < 0:
            self.dy = 1
            return
        brick = self.brick_at(self.ball_x, ny)
        if brick:
            self._hit(*brick)
            self.dy = -self.dy
            return
        if ny == self.paddle_row:
            offset = self.ball_x - self.paddle_x
            if 0 <= offset < PADDLE_W:
                self.dx = PADDLE_ZONES[offset]
                self.dy = -1
            else:
                self._lose_ball()
            return
        self.ball_y = ny

    def _lose_ball(self):
        self.lives -= 1
        if self.lives <= 0:
            self.alive = False
            return
        self._set_effect(f"BALL LOST! {self.lives} left")
        self._serve()

    def _next_level(self):
        self.level += 1
        cleared = self.level - self.start_level
        self._set_effect(f"LEVEL {self.level}!")
        if cleared % EXTRA_LIFE_EVERY == 0 and self.lives < MAX_LIVES:
            self.lives += 1
            self._set_effect(f"LEVEL {self.level}! +1 LIFE")
        self._build_wall()
        self._serve()

    def step(self):
        """Advance one tick. Returns True if the run goes on."""
        if not self.active:
            return self.alive

        self.ticks += 1
        # Paused ticks never get here, so pausing does not burn the banner.
        self.elapsed_ms += self.delay_ms
        if self.effect and self.elapsed_ms >= self.effect_until_ms:
            self.effect = ""

        if self.launched:
            self._move_ball()
            if self.alive and not self.bricks_left:
                self._next_level()
        return self.alive

    # Saved sessions. `Game` never touches curses, so its whole state is plain
    # data and parking a session is just writing that data out.

    def to_dict(self):
        """Everything needed to resume this run later, as plain JSON."""
        return {
            "version": SAVE_VERSION,
            "width": self.width,
            "height": self.height,
            # One string per brick row: "." for gone, else hits left.
            "bricks": ["".join(str(h) if h else "." for h in row)
                       for row in self.bricks],
            "paddle_x": self.paddle_x,
            "ball_x": self.ball_x,
            "ball_y": self.ball_y,
            "dx": self.dx,
            "dy": self.dy,
            "launched": self.launched,
            "score": self.score,
            "lives": self.lives,
            "level": self.level,
            "start_level": self.start_level,
            "ticks": self.ticks,
            "elapsed_ms": self.elapsed_ms,
            "effect": self.effect,
            "effect_until_ms": self.effect_until_ms,
        }

    def load_dict(self, data):
        """Restore a run from to_dict() output. False if it is unusable.

        Everything is parsed into locals and only assigned once all of it has
        checked out, so a rejected save leaves a playable fresh game rather
        than a half-restored one.
        """
        self.reset()
        try:
            if data["version"] != SAVE_VERSION:
                return False
            if data["width"] != self.width or data["height"] != self.height:
                return False   # saved under different WIDTH/HEIGHT constants

            rows = data["bricks"]
            if len(rows) != BRICK_ROWS:
                raise ValueError("wrong number of brick rows")
            bricks = []
            for row in rows:
                if len(row) != self.cols:
                    raise ValueError("wrong row width")
                if any(c not in ".12" for c in row):
                    raise ValueError("bad brick")
                bricks.append([0 if c == "." else int(c) for c in row])
            if not any(any(row) for row in bricks):
                raise ValueError("no bricks left")
            paddle_x = int(data["paddle_x"])
            if not 0 <= paddle_x <= self.width - PADDLE_W:
                raise ValueError("paddle off the field")
            ball_x, ball_y = int(data["ball_x"]), int(data["ball_y"])
            if not (0 <= ball_x < self.width
                    and 0 <= ball_y < self.paddle_row):
                raise ValueError("ball off the field")
            dx, dy = int(data["dx"]), int(data["dy"])
            if dx not in PADDLE_ZONES or dy not in (-1, 1):
                raise ValueError("bad ball direction")
            launched = bool(data["launched"])
            score = int(data["score"])
            lives = int(data["lives"])
            if not 1 <= lives <= MAX_LIVES:
                raise ValueError("bad lives")
            start_level = int(data["start_level"])
            if not 1 <= start_level <= MAX_START_LEVEL:
                raise ValueError("bad start level")
            level = int(data["level"])
            if level < start_level:
                raise ValueError("bad level")
            ticks = int(data["ticks"])
            elapsed_ms = int(data["elapsed_ms"])
            effect = str(data["effect"])
            effect_until_ms = int(data["effect_until_ms"])
        except (KeyError, TypeError, ValueError):
            return False

        self.bricks = bricks
        # The ball must not sit inside a brick; a hand-edited file that puts
        # it there would have it chew through the wall from the inside.
        if self.brick_at(ball_x, ball_y):
            self.reset()
            return False
        self.paddle_x = paddle_x
        self.ball_x, self.ball_y = ball_x, ball_y
        self.dx, self.dy = dx, dy
        self.launched = launched
        if not launched:
            self._carry_ball()
        self.score = score
        self.lives = lives
        self.level = level
        self.start_level = start_level
        self.ticks = ticks
        self.elapsed_ms = elapsed_ms
        self.effect = effect
        self.effect_until_ms = effect_until_ms
        return True


def data_dir():
    """Where the game keeps its files, honouring XDG_DATA_HOME."""
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    return os.path.join(base, "breakout")


def high_score_path():
    """Where the best score is kept."""
    return os.path.join(data_dir(), "highscore")


def session_path():
    """Where a paused game is parked between runs."""
    return os.path.join(data_dir(), "session")


def _write_atomically(path, text):
    """Write to a temp file and rename over the target, so an interrupted
    write cannot leave a truncated file behind. False if it did not land."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


class HighScore:
    """Best score, persisted between runs.

    Deliberately never raises: a missing, corrupt, or unwritable file just
    means the record lives for this session only. Losing a high score is not
    worth crashing a game over.
    """

    def __init__(self, path=None):
        self.path = path or high_score_path()
        self.value = self._read()
        self.persisted = True

    def _read(self):
        try:
            with open(self.path) as fh:
                return max(0, int(fh.read().strip()))
        except (OSError, ValueError):
            return 0

    def _write(self, score):
        return _write_atomically(self.path, f"{score}\n")

    def submit(self, score):
        """Record a finished run. Returns True if it set a new record."""
        if score <= self.value:
            return False
        self.value = score
        self.persisted = self._write(score)
        return True


class SavedSession:
    """A paused game, parked on disk between runs.

    Follows HighScore in never raising: a missing or corrupt save just means
    there is nothing to resume. The one result that is not ignored is save()'s
    — the caller is about to exit and must not do so if the run never landed.
    """

    def __init__(self, path=None):
        self.path = path or session_path()

    def load(self):
        """The saved state, or None if there is no usable one."""
        try:
            with open(self.path) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def save(self, game):
        """Park `game`. Returns True if it reached the disk."""
        return _write_atomically(self.path, json.dumps(game.to_dict()) + "\n")

    def clear(self):
        """Consume the save, so the same run cannot be resumed twice."""
        try:
            os.remove(self.path)
        except OSError:
            pass


LEFT_KEYS = {curses.KEY_LEFT, ord("a"), ord("A"), ord("h")}
RIGHT_KEYS = {curses.KEY_RIGHT, ord("d"), ord("D"), ord("l")}
LAUNCH_KEYS = {ord(" "), curses.KEY_UP, ord("w"), ord("W"), ord("k")}

# Rainbow wall, warm at the top where the points are.
ROW_COLORS = [
    curses.COLOR_RED,
    curses.COLOR_MAGENTA,
    curses.COLOR_YELLOW,
    curses.COLOR_GREEN,
    curses.COLOR_CYAN,
    curses.COLOR_BLUE,
]

class Glide:
    """Turns a held arrow key into smooth paddle motion. No curses here.

    A terminal sends a held key as a burst of repeats, typically 30 a second
    after an initial delay, and never says when the key comes up. Moving the
    paddle once per repeat makes its speed whatever the OS repeat rate is,
    and steppy with it. Instead, a repeat of the same direction marks the key
    as held, and while it is held the paddle moves at PADDLE_SPEED on the
    clock, every frame. The key counts as released once no repeat has come in
    for one and a half repeat gaps (measured, not assumed, since repeat rates
    vary), so letting go stops the paddle within a frame or two.
    """

    def __init__(self):
        self.stop()

    def stop(self):
        self.dir = 0
        self.held = False
        self.last_key = 0.0
        self.until = 0.0
        self.last_frame = 0.0
        self.carry = 0.0

    def press(self, direction, now):
        """A left (-1) or right (1) key arrived. Returns columns to move
        right away: a tap's step on a fresh press, nothing on a repeat,
        since a held paddle moves on the clock in advance()."""
        repeat = (direction == self.dir and now <= self.until)
        if repeat:
            gap = now - self.last_key
            if not self.held:
                self.held = True
                self.last_frame = now
                self.carry = 0.0
            self.until = now + min(gap * 1.5, MAX_REPEAT_GAP_MS / 1000)
            self.last_key = now
            return 0
        # Fresh press. Wait long enough for the OS's first repeat, which
        # comes after a longer delay than the rest; if it never does, this
        # was a tap and nothing further moves.
        self.stop()
        self.dir = direction
        self.last_key = now
        self.until = now + 0.6
        return direction * PADDLE_STEP

    def advance(self, now):
        """Columns to move for the time since the last frame."""
        if not self.held:
            if self.dir and now > self.until:
                self.stop()
            return 0
        end = min(now, self.until)
        self.carry += (end - self.last_frame) * PADDLE_SPEED
        self.last_frame = end
        cols = int(self.carry)
        self.carry -= cols
        if now >= self.until:
            self.stop()
        return self.dir * cols

    @property
    def gliding(self):
        return self.held


HELP = [
    " Left/Right or A/D move  Space/Up launch  P pause",
    " R restart  X save&exit  Q quit",
]
PANEL_W = 20


def _color(colors, name):
    """Attribute for `name`, falling back to plain bold if unset."""
    return colors.get(name, curses.A_BOLD)


def _put(win, y, x, text, attr=0):
    """addstr that tolerates the bottom-right cell and any clipping."""
    try:
        win.addstr(y, x, text, attr)
    except curses.error:
        pass


def draw(stdscr, game, colors, high, record=False):
    stdscr.erase()
    w = game.width

    _put(stdscr, 0, 0, "+" + "-" * w + "+")
    for y in range(game.height):
        _put(stdscr, y + 1, 0, "|")
        _put(stdscr, y + 1, w + 1, "|")
    _put(stdscr, game.height + 1, 0, "+" + "-" * w + "+")

    for row, hits_row in enumerate(game.bricks):
        for col, hits in enumerate(hits_row):
            if hits:
                # A two-hit brick shows its armour until the first hit.
                face = "[##]" if hits > 1 else "[==]"
                _put(stdscr, BRICK_TOP + row + 1, col * BRICK_W + 1, face,
                     _color(colors, row))

    _put(stdscr, game.paddle_row + 1, game.paddle_x + 1,
         "<" + "=" * (PADDLE_W - 2) + ">", _color(colors, "paddle"))
    if game.alive:
        _put(stdscr, game.ball_y + 1, game.ball_x + 1, "O",
             _color(colors, "ball"))

    # Side panel. While a run is beating the record, show it climbing rather
    # than the stale stored value.
    px = w + 4
    best = max(high.value, game.score)
    _put(stdscr, 1, px, "BREAKOUT", curses.A_BOLD)
    _put(stdscr, 3, px, f"Score:  {game.score}", curses.A_BOLD)
    _put(stdscr, 4, px, f"Best:   {best}"
         + ("" if high.persisted else " (unsaved)"), curses.A_BOLD)
    _put(stdscr, 6, px, f"Lives:  {game.lives}", curses.A_BOLD)
    _put(stdscr, 7, px, f"Level:  {game.level}", curses.A_BOLD)
    _put(stdscr, 8, px, f"Bricks: {game.bricks_left}", curses.A_BOLD)
    if game.effect:
        _put(stdscr, 10, px, f" {game.effect} ",
             curses.A_REVERSE | curses.A_BOLD)
    if game.alive and not game.launched and not game.paused:
        _put(stdscr, 12, px, "Space to launch", curses.A_DIM)

    for i, line in enumerate(HELP):
        _put(stdscr, game.height + 2 + i, 0, line)

    if not game.alive or game.paused:
        if not game.alive:
            msg = [" GAME OVER ", f" score {game.score} "]
            if record:
                msg.append(" NEW BEST! ")
            msg.append(" R restart  Q quit ")
        else:
            msg = [" PAUSED "]
        # Below the wall, so the banner never hides what is left of it.
        top = BRICK_TOP + BRICK_ROWS + 3
        for i, line in enumerate(msg):
            x = max(1, (w + 2 - len(line)) // 2)
            _put(stdscr, top + i, x, line, curses.A_REVERSE | curses.A_BOLD)

    stdscr.noutrefresh()
    curses.doupdate()


def run(stdscr, resume=False, start_level=1):
    curses.curs_set(0)
    stdscr.keypad(True)
    stdscr.nodelay(True)

    colors = {}
    if curses.has_colors():
        try:
            curses.start_color()
            curses.use_default_colors()  # not supported everywhere
            for row, color in enumerate(ROW_COLORS):
                curses.init_pair(row + 1, color, -1)
                colors[row] = curses.color_pair(row + 1) | curses.A_BOLD
            curses.init_pair(len(ROW_COLORS) + 1, curses.COLOR_WHITE, -1)
            white = curses.color_pair(len(ROW_COLORS) + 1) | curses.A_BOLD
            colors["paddle"] = white
            colors["ball"] = white
        except curses.error:
            colors = {}  # keep the monochrome bold fallback

    game = Game(start_level=start_level)
    need_rows = game.height + 2 + len(HELP)
    need_cols = max(game.width + 2 + PANEL_W, max(map(len, HELP)))
    rows, cols = stdscr.getmaxyx()
    if rows < need_rows or cols < need_cols:
        raise SystemExit(
            f"Terminal too small: need at least {need_cols}x{need_rows}, "
            f"got {cols}x{rows}. Resize and try again."
        )

    session = SavedSession()
    if resume:
        # Deliberately after the size check: a terminal too small to draw the
        # field must not eat the save on its way out.
        data = session.load()
        if data is not None and game.load_dict(data):
            # Land paused. You are returning to a ball you have not looked at
            # in hours; unpausing is one keypress.
            game.paused = True
        else:
            game._set_effect("NO SAVED SESSION")
        # Consumed either way, so a rejected save does not sit there failing
        # on every -c from now on.
        session.clear()

    high = HighScore()
    draw(stdscr, game, colors, high)

    saved = False
    try:
        saved = _loop(stdscr, game, colors, high, session)
    finally:
        # Quitting or Ctrl-C mid-run still banks the score. submit() ignores
        # anything that does not beat the record, so this is safe to repeat.
        high.submit(game.score)
    return saved


def _loop(stdscr, game, colors, high, session):
    """Play until the player quits. True if they saved on the way out."""
    record = False  # did the run now on screen set a new best?
    next_tick = time.monotonic() + game.delay_ms / 1000
    glide = Glide()

    while True:
        # Wait for input only until the next tick is due. getch() returns as
        # soon as a key arrives, so the clock — not the input — decides when
        # the ball moves; otherwise holding an arrow key would speed it up.
        remaining_ms = int((next_tick - time.monotonic()) * 1000)
        if glide.gliding:
            # A held paddle moves between key repeats too, so wake up often
            # enough to draw it gliding rather than hopping.
            remaining_ms = min(remaining_ms, GLIDE_FRAME_MS)
        stdscr.timeout(max(0, remaining_ms))
        key = stdscr.getch()

        # Drain the rest of the buffer with no delay. timeout() overrides
        # nodelay(), so this switch has to happen every pass: without it a
        # steady stream of keys never empties the buffer and the game hangs.
        stdscr.nodelay(True)
        was_alive = game.alive
        dirty = False
        while key != -1:
            dirty = True
            if key in (ord("q"), ord("Q")):
                return False
            elif key in (ord("x"), ord("X")):
                # Pause, park the run, leave. A failed write must not exit:
                # silently binning the run the player was trying to protect is
                # worse than staying put and saying so. Nothing to save once
                # the run is over, so on a dead board X does nothing.
                if game.alive:
                    game.paused = True
                    if session.save(game):
                        return True
                    game._set_effect("SAVE FAILED")
            elif key in (ord("r"), ord("R")):
                glide.stop()
                high.submit(game.score)
                game.reset()
                record = False
                was_alive = True
            elif key in (ord("p"), ord("P")):
                if game.alive:
                    game.paused = not game.paused
            elif key in LEFT_KEYS or key in RIGHT_KEYS:
                cols = glide.press(-1 if key in LEFT_KEYS else 1,
                                   time.monotonic())
                if cols:
                    game.move(cols)
            elif key in LAUNCH_KEYS:
                if game.launch():
                    # A full tick before the first move, not the scrap left
                    # of whatever tick was running while it sat waiting.
                    next_tick = time.monotonic() + game.delay_ms / 1000
            key = stdscr.getch()

        now = time.monotonic()
        cols = glide.advance(now)
        if cols and game.move(cols):
            dirty = True

        if now >= next_tick:
            game.step()
            next_tick = now + game.delay_ms / 1000
            dirty = True

        if was_alive and not game.alive:  # the run just ended
            record = high.submit(game.score)

        if dirty:
            draw(stdscr, game, colors, high, record)


def main():
    parser = argparse.ArgumentParser(
        description="Breakout for the terminal.",
        epilog="Press X in game to pause, save and exit.",
    )
    # dest is not cosmetic: args.continue would be a syntax error.
    parser.add_argument(
        "-c", "--continue", dest="resume", action="store_true",
        help="resume the session last saved with X",
    )
    parser.add_argument(
        "-l", "--level", type=int, default=1, metavar="N",
        choices=range(1, MAX_START_LEVEL + 1),
        help=f"starting level, 1-{MAX_START_LEVEL} (default 1)",
    )
    args = parser.parse_args()

    saved = False
    try:
        saved = curses.wrapper(run, resume=args.resume, start_level=args.level)
    except KeyboardInterrupt:
        pass
    if saved:
        # Echo back however it was started: `breakout` once symlinked onto
        # PATH, `python3 breakout.py` from a checkout.
        prog = os.path.basename(sys.argv[0])
        cmd = prog if prog != "breakout.py" else "python3 breakout.py"
        print(f"Session saved. Resume with: {cmd} -c")


if __name__ == "__main__":
    main()
