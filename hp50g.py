#!/usr/bin/env python3
"""Drive a real HP 50g over USB from the Mac (no drivers needed).

On the calculator: type XSERV and press ENTER (XModem server mode).
Then, from here:

    ./hp                        interactive REPL (shows the calc's stack)
    ./hp eval "100 !"           run a command line, print what it returned
                                (the calc's stack is left exactly as it was)
    ./hp exec "1 'X' STO"       run a command line, leave results on the stack
    ./hp stack                  print the calc's stack
    ./hp ls                     variables in the current directory
    ./hp mem                    free memory (bytes)
    ./hp get NAME [-o FILE]     fetch a variable (decoded, or raw to FILE)

HP special characters can be typed as Unicode (« » → √ π ≤ Σ ...) or as the
standard HP trigraphs (\\<< \\>> \\-> \\v/ \\pi \\<= \\GS ...).

See CLAUDE.md for the protocol notes behind this.
"""
import argparse, fcntl, os, sys
import usb.core, usb.util, usb.backend.libusb1

# One process at a time: two talking to XSERV would interleave bytes and
# can crash the server. Held for the life of the process (a whole REPL).
LOCK_PATH = os.path.expanduser("~/.hp50g.lock")
VID, PID = 0x03F0, 0x0121          # "HPx9G+ Device", HP Company
EP_IN, EP_OUT = 0x81, 0x03         # bulk, 64-byte packets
LIBUSB = "/opt/homebrew/lib/libusb-1.0.dylib"
SOH, STX, EOT, ACK, NAK = b"\x01", b"\x02", b"\x04", b"\x06", b"\x15"
# Scratch variables created (and purged) in the calc's current directory.
V_OUT, V_ERR, V_DEP = "CLTMP", "CLERR", "CLDEP"


class CalcError(Exception):
    """An error raised by the calculator while running a command line."""


# ---- HP character set ------------------------------------------------------
# 128-159 are HP-specific; the rest matches Latin-1 (« = 171, » = 187).
HP_CHARS = "∡x̄∇√∫Σ▶π∂≤≥≠α→←↓↑γδεηθλρστωΔΠΩ■∞"
HP_CHARS = ["∡", "x̄"] + list(HP_CHARS[3:])       # x̄ is two code points
TO_UNI = {128 + i: ch for i, ch in enumerate(HP_CHARS)}
FROM_UNI = {ch: code for code, ch in TO_UNI.items()}
TRIGRAPHS = {
    r"\<<": "«", r"\>>": "»", r"\->": "→", r"\<-": "←", r"\v/": "√",
    r"\pi": "π", r"\<=": "≤", r"\>=": "≥", r"\=/": "≠", r"\GS": "Σ",
    r"\oo": "∞", r"\.d": "∂", r"\<)": "∡", r"\Ga": "α", r"\Gb": "β",
    r"\Gd": "δ", r"\Ge": "ε", r"\Gh": "θ", r"\Gl": "λ", r"\Gm": "μ",
    r"\Gr": "ρ", r"\Gs": "σ", r"\Gt": "τ", r"\Gw": "ω", r"\GD": "Δ",
    r"\PI": "Π", r"\GW": "Ω", r"\.S": "∫", r"\|>": "▶", r"\x-": "x̄",
}

def to_hp(text):
    for tri, ch in TRIGRAPHS.items():
        text = text.replace(tri, ch)
    text = text.replace("x̄", "\x80")              # before per-char mapping
    out = bytearray()
    for ch in text:
        if ch in FROM_UNI:
            out.append(FROM_UNI[ch])
        elif ord(ch) < 256:
            out.append(ord(ch))
        else:
            raise ValueError(f"character {ch!r} has no HP 50g equivalent")
    return bytes(out)

def from_hp(data):
    return "".join(TO_UNI.get(b, chr(b)) for b in data)


def acquire_lock(wait=False):
    """Exclusive flock on LOCK_PATH; the returned file must stay open.
    The kernel releases it when the process exits, even on a crash."""
    f = open(LOCK_PATH, "a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
    except BlockingIOError:
        f.seek(0)
        holder = f.read().strip() or "another process"
        sys.exit(f"HP 50g is in use by {holder} (use --wait to queue)")
    f.seek(0)
    f.truncate()
    f.write(f"pid {os.getpid()}: {' '.join(sys.argv)}\n")
    f.flush()
    return f


class Calc:
    def __init__(self, wait=False):
        self.lock = acquire_lock(wait)
        be =usb.backend.libusb1.get_backend(find_library=lambda _: LIBUSB)
        self.d = usb.core.find(idVendor=VID, idProduct=PID, backend=be)
        if self.d is None:
            sys.exit("HP 50g not found on USB (plugged in and switched on?)")
        self.d.set_configuration()
        usb.util.claim_interface(self.d, 0)
        self.buf = b""
        self.drain(300)

    def close(self):
        usb.util.release_interface(self.d, 0)
        self.lock.close()                # releases the flock

    # ---- raw byte pipe -------------------------------------------------
    def write(self, data):
        self.d.write(EP_OUT, data, timeout=2000)

    def _fill(self, t):
        try:
            self.buf += bytes(self.d.read(EP_IN, 64, timeout=t))
            return True
        except usb.core.USBTimeoutError:
            return False

    def read(self, n, t=3000):
        while len(self.buf) < n:
            if not self._fill(t):
                raise TimeoutError(f"wanted {n} bytes, have {self.buf!r}")
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def drain(self, t=300):
        while self._fill(t):
            pass
        out, self.buf = self.buf, b""
        return out

    # ---- server packets: 2-byte BE length, data, 1-byte sum checksum ---
    @staticmethod
    def pkt(data):
        return len(data).to_bytes(2, "big") + data + bytes([sum(data) & 0xFF])

    def packet(self):
        n = int.from_bytes(self.read(2), "big")
        data, chk = self.read(n), self.read(1)[0]
        ok = sum(data) & 0xFF == chk
        self.write(ACK if ok else NAK)   # without ACK the calc resends forever
        if not ok:
            raise IOError(f"bad checksum on {data!r}")
        return data

    # ---- raw server commands (M, L, E, G) -------------------------------
    def mem(self):
        self.write(b"M")
        return int(float(self.packet()))

    def sync(self, tries=12):
        """Wait until the server is idle. Commands sent while the calc is
        still busy (e.g. right after E) are silently dropped."""
        for _ in range(tries):
            self.drain(200)
            try:
                self.mem()
                self.drain(300)
                return
            except TimeoutError:
                pass
        raise IOError("calculator not answering - on the calc, type XSERV "
                      "and press ENTER (press ON first if it shows an error)")

    def ls(self):
        self.write(b"L")
        d, out, i = self.packet(), [], 0
        while i < len(d):
            n = d[i]
            name = from_hp(d[i + 1:i + 1 + n])
            i += 1 + n
            prolog = int.from_bytes(d[i:i + 2], "little")
            size = int.from_bytes(d[i + 2:i + 5], "little")
            out.append((name, prolog, size))
            i += 7                       # 2 prolog + 3 size + 2 crc
        return out

    def _execute_raw(self, cmd: bytes):
        """E must be followed *immediately* by the framed command line.
        DANGER: any error while it runs aborts XSERV on the calc - only
        send command lines that cannot fail (see run())."""
        self.write(b"E" + self.pkt(cmd))
        if self.read(1, t=10000) != ACK:
            raise IOError("execute refused")
        self.sync()

    def _get_raw(self, name):
        """G + framed name -> calc ACKs, then acts as XModem sender.
        DANGER: a missing variable aborts XSERV - only call for names
        known to exist (see get())."""
        self.write(b"G" + self.pkt(to_hp(name)))
        if self.read(1) != ACK:
            raise IOError(f"get {name} refused")
        raw = self._xrecv()
        self.sync()
        return raw

    def _xrecv(self):
        """XModem receive, checksum mode. NAK repeatedly until the sender
        starts (the first NAK is often too early and gets ignored)."""
        for _ in range(15):
            self.write(NAK)
            if self._fill(1000):
                break
        else:
            raise IOError("XModem sender never started")
        out, expect = b"", 1
        while True:
            h = self.read(1, t=5000)
            if h == EOT:
                self.write(ACK)
                return out
            size = {SOH: 128, STX: 1024}.get(h)
            if size is None:
                raise IOError(f"unexpected XModem header {h!r}")
            n, nn = self.read(2)
            blk, chk = self.read(size), self.read(1)[0]
            if n + nn != 0xFF or sum(blk) & 0xFF != chk:
                self.write(NAK)
                continue
            if n == expect & 0xFF:
                out += blk
                expect += 1
            self.write(ACK)

    # ---- safe high-level API ---------------------------------------------
    def get(self, name):
        if name not in {n for n, _, _ in self.ls()}:
            raise KeyError(f"{name} not in the current directory")
        return self._get_raw(name)

    def run(self, line, keep=True):
        """Run a command line safely and return (results, stack).

        The line is sent as a string and executed with STR→ inside IFERR,
        so neither syntax nor runtime errors can kill XSERV; they are
        raised here as CalcError. Every object comes back as its →STR
        text.
          keep=True:  results stay on the calc; `stack` is the whole stack
                      (level 1 last), `results` is None.
          keep=False: whatever the line pushed is removed again and
                      returned as `results`; `stack` is None.
        """
        # HP strings can't contain '"': rebuild it with 34 CHR on the calc.
        parts = line.split('"')
        src = f'"{parts[0]}"' + "".join(f' 34 CHR + "{p}" +' for p in parts[1:])
        tostr = ("IF DUP SIZE THEN 1 « DUP TYPE 2 == SWAP →STR SWAP "
                 "IF THEN 34 CHR SWAP + 34 CHR + END » DOLIST END")
        prog = (
            f"DEPTH '{V_DEP}' STO "
            f"IFERR {src} STR→ THEN ERRM ELSE \"\" END '{V_ERR}' STO "
            + (f"DEPTH →LIST DUP {tostr} " if keep else
               f"DEPTH {V_DEP} - 0 MAX →LIST {tostr} ")
            + f"{V_ERR} SWAP + '{V_OUT}' STO "
            + ("OBJ→ DROP " if keep else "")
            + f"IFERR {{ {V_ERR} {V_DEP} }} PURGE THEN END"
        )
        self._execute_raw(to_hp(prog))
        items = decode(self.get(V_OUT))   # checks it exists before G
        self._execute_raw(to_hp(f"IFERR '{V_OUT}' PURGE THEN END"))
        err, objs = items[0], items[1:]
        if err:
            raise CalcError(err)
        return (None, objs) if keep else (objs, None)

    def screen(self):
        """Grab the display (LCD→) as rows of 0/1 pixels, 131 x 80."""
        self._execute_raw(to_hp(f"LCD→ '{V_OUT}' STO"))
        img = decode(self.get(V_OUT))
        self._execute_raw(to_hp(f"IFERR '{V_OUT}' PURGE THEN END"))
        return img

    def eval(self, line):
        return self.run(line, keep=False)[0]

    def stack(self):
        return self.run("", keep=True)[1]


# ---- object decoding (HPHP49-? header, then nibbles LSN-first) -------------
def _nibbles(b):
    return [x for byte in b for x in (byte & 0xF, byte >> 4)]

def _num(nb):
    return sum(n << (4 * i) for i, n in enumerate(nb))

PROLOG_NAMES = {
    0x02614: "integer", 0x02933: "real", 0x02A2C: "string", 0x02A74: "list",
    0x02D9D: "program", 0x02AB8: "algebraic", 0x02A96: "directory",
    0x02686: "matrix", 0x029E8: "array", 0x02E48: "global name",
    0x02977: "complex", 0x02B1E: "graphic", 0x02B88: "library",
}

def _obj(nb, i):
    """Decode the object at nibble i -> (value, next index)."""
    prolog = _num(nb[i:i + 5])
    if prolog == 0x02614:                # exact integer
        size = _num(nb[i + 5:i + 10])    # nibbles, counting the size field
        digits = nb[i + 10:i + 5 + size]
        sign, digits = digits[-1], digits[:-1]
        v = int("".join(map(str, reversed(digits))) or "0")
        return (-v if sign == 9 else v), i + 5 + size
    if prolog == 0x02933:                # real: 3 exp, 12 mantissa, 1 sign
        e = int("".join(map(str, reversed(nb[i + 5:i + 8]))))
        if e >= 500:
            e -= 1000                    # 10's complement
        m = "".join(map(str, reversed(nb[i + 8:i + 20])))
        s = "-" if nb[i + 20] == 9 else ""
        return float(f"{s}{m[0]}.{m[1:]}e{e}"), i + 21
    if prolog == 0x02A2C:                # string
        size = _num(nb[i + 5:i + 10])
        ch = nb[i + 10:i + 5 + size]
        data = bytes(ch[k] | ch[k + 1] << 4 for k in range(0, len(ch), 2))
        return from_hp(data), i + 5 + size
    if prolog == 0x02A74:                # list: objects until SEMI
        out, i = [], i + 5
        while _num(nb[i:i + 5]) != 0x0312B:
            v, i = _obj(nb, i)
            out.append(v)
        return out, i + 5
    if prolog == 0x02B1E:                # graphic: size, height, width, rows
        size = _num(nb[i + 5:i + 10])
        h, w = _num(nb[i + 10:i + 15]), _num(nb[i + 15:i + 20])
        row = (w + 7) // 8 * 2           # nibbles per row (byte aligned)
        px = nb[i + 20:i + 20 + row * h]
        img = [[px[y * row + x // 4] >> (x % 4) & 1 for x in range(w)]
               for y in range(h)]
        return img, i + 5 + size
    kind = PROLOG_NAMES.get(prolog, f"prolog 0{prolog:04X}")
    raise ValueError(f"can't decode {kind} objects; use eval with →STR")

def decode(raw):
    if raw[:6] != b"HPHP49":
        raise ValueError(f"no HPHP49 header: {raw[:8]!r}")
    return _obj(_nibbles(raw[8:]), 0)[0]


def render(img):
    """Two pixel rows per text line using half blocks."""
    out = []
    for y in range(0, len(img), 2):
        top, bot = img[y], img[y + 1] if y + 1 < len(img) else [0] * len(img[y])
        out.append("".join(" ▀▄█"[t | b << 1] for t, b in zip(top, bot)))
    return "\n".join(out)


# ---- REPL ------------------------------------------------------------------
REPL_HELP = """\
Each line runs on the calculator, as if typed there; the stack is shown after.
  :ls  :mem  :get NAME  :help  :quit (or Ctrl-D)
Type « » → √ π etc. directly or as trigraphs \\<< \\>> \\-> \\v/ \\pi.
A line with an unclosed « continues on the next line."""

def show_stack(stack, levels=8):
    if not stack:
        print("  (empty stack)")
        return
    shown = stack[-levels:]
    if len(stack) > levels:
        print(f"  ... {len(stack) - levels} more")
    for lvl, item in zip(range(len(shown), 0, -1), shown):
        print(f"  {lvl}: {item}")

def repl(c):
    try:
        import readline
        hist = os.path.expanduser("~/.hp50g_history")
        try:
            readline.read_history_file(hist)
        except OSError:
            pass
    except ImportError:
        readline = None
    print("HP 50g REPL — :help for help")
    show_stack(c.stack())
    while True:
        try:
            line = input("hp> ")
            while (line.count("«") + line.count(r"\<<")
                   > line.count("»") + line.count(r"\>>")):
                line += "\n" + input("..> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        line = line.strip()
        if not line:
            continue
        try:
            if line in (":q", ":quit", ":exit"):
                break
            elif line == ":help":
                print(REPL_HELP)
            elif line == ":ls":
                for name, prolog, size in c.ls():
                    kind = PROLOG_NAMES.get(prolog, f"0{prolog:04X}")
                    print(f"  {name:12} {kind:12} {size:>7} bytes")
            elif line == ":mem":
                print(f"  {c.mem()} bytes free")
            elif line.startswith(":get "):
                print(" ", decode(c.get(line[5:].strip())))
            else:
                show_stack(c.run(line)[1])
        except CalcError as e:
            print(f"  Error: {e}")
        except (KeyError, ValueError) as e:
            print(f"  {e}")
    if readline:
        try:
            readline.write_history_file(hist)
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wait", action="store_true",
                    help="if another hp process is using the calc, wait for it")
    sub = ap.add_subparsers(dest="cmd")
    for name in ("mem", "ls", "stack", "repl", "screen"):
        sub.add_parser(name)
    sub.add_parser("eval").add_argument("line")
    sub.add_parser("exec").add_argument("line")
    g = sub.add_parser("get")
    g.add_argument("name")
    g.add_argument("-o", metavar="FILE", help="save the raw object instead")
    a = ap.parse_args()

    c = Calc(wait=a.wait)
    try:
        if a.cmd in (None, "repl"):
            c.sync()
            repl(c)
        elif a.cmd == "mem":
            print(c.mem())
        elif a.cmd == "ls":
            for name, prolog, size in c.ls():
                print(f"{name:12} {PROLOG_NAMES.get(prolog, hex(prolog)):12} {size:>7}")
        elif a.cmd == "stack":
            show_stack(c.stack(), levels=1000)
        elif a.cmd == "screen":
            print(render(c.screen()))
        elif a.cmd == "eval":
            for item in c.eval(a.line):
                print(item)
        elif a.cmd == "exec":
            c.run(a.line)
        elif a.cmd == "get":
            raw = c.get(a.name)
            if a.o:
                open(a.o, "wb").write(raw)
            else:
                print(decode(raw))
    except CalcError as e:
        sys.exit(f"Error: {e}")
    except (KeyError, IOError, ValueError) as e:
        sys.exit(str(e))
    finally:
        c.close()


if __name__ == "__main__":
    main()
