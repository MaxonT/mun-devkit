"""A session simulator: run a game on a development computer with the
contracts a MUN console gives it, and nothing more.

What it reproduces, following MUN OS's documents (docs/runtime.md,
docs/saves.md) and the behaviour of its launcher and card service:

- the session's layout: a copy of the entry run with no arguments, `work/`
  as working directory and HOME, the facts in the environment;
- single-object saves: the restore rules, the request checks in their order,
  the stable codes with the console's sentences, one request per connection,
  one save at a time, the bounds on a line and on a payload and how they are
  measured, the envelope on the "card", the previous copy;
- the end of a session: SIGTERM, a grace period, SIGKILL; the result as the
  console words it (exited, failed, crashed).

It follows the MUN OS release named in mun-os.lock.json. An earlier console
may differ in a detail (the previews before v0.1.0-dev.5 measured a payload
with every character outside ASCII escaped, and read a shorter line).

What it does not reproduce, and therefore says nothing about: the sandbox
(user, device rules, read-only system, `noexec` content, memory and task
limits), the display, sound and input devices, the real card and its
failures, the shell. A game that passes here follows the contracts; whether
it runs on a console is established on a console.

Faults are injected by name (`fail`): the simulated card then answers a save
with that code, as the console would word it. `no_space` here is an injected
answer, not a full file system."""

import errno
import json
import os
import selectors
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .errors import MdkError

SAVE_MAX_BYTES = 64 * 1024               # a game's payload, measured as compact JSON in UTF-8 (docs/saves.md)
# The request line the console accepts: the payload may spell every
# character as a \uXXXX escape, six bytes for one.
SAVE_LINE_MAX = 6 * SAVE_MAX_BYTES + 4096
JSON_MAX_DEPTH = 64                      # a line nested deeper is not read
SAVE_REQUEST_TIMEOUT = 10.0              # a connection that asks nothing for this long is closed
SAVE_CLIENTS_MAX = 8                     # connections held at once
STOP_GRACE = 3.0                         # seconds between SIGTERM and SIGKILL (docs/runtime.md)
SAVE_FORMATS = {"earlier": "neptune-save/1", "mun": "mun-save/1"}
PROFILE_ENVIRONMENT = {
    "linux-arm64-v0": {},
    "linux-arm64-gl-v0": {"SDL_VIDEODRIVER": "kmsdrm", "SDL_AUDIODRIVER": "alsa", "ALSOFT_DRIVERS": "alsa"},
}
# The console's sentence for each code a card answers a save with.
CARD_FAULTS = {
    "no_space": "No hay espacio en la Game Card para guardar la partida",
    "sync_failed": "La Game Card devolvió un error al escribir",
    "write_failed": "No se pudo escribir la partida",
    "card_removed": "La tarjeta se retiró mientras se guardaba",
    "card_unavailable": "La tarjeta ya no está disponible",
    "card_mismatch": "La tarjeta activa no es la de la sesión",
    "save_path_unsafe": "La ruta de partidas de la tarjeta contiene un enlace o no es una carpeta",
}
# Answered by the launcher before the card is asked.
LAUNCHER_FAULTS = {"reader_unavailable": "El lector de tarjetas no está disponible",
                   "save_failed": "El lanzador no pudo atender el guardado"}
TOO_LARGE = "La partida supera el tamaño permitido"
FAULTS = {**CARD_FAULTS, **LAUNCHER_FAULTS}


class SimCard:
    """The save area of a card, as a directory: `<root>/saves/<id>/save.json`
    and its previous copy, written and read by the console's rules."""

    def __init__(self, root: Path, card_id: str, version: str, saves_rules, naming: str = "mun"):
        self.root = Path(root)
        self.info = {"id": card_id, "version": version}
        self.format = SAVE_FORMATS[naming]
        self.rules = saves_rules            # mun_card.saves, from the MUN OS checkout
        self.insertion = uuid.uuid4().hex[:16]
        self.directory = self.root / "saves" / card_id

    # -- reading ---------------------------------------------------------
    def _read(self, name: str) -> Optional[bytes]:
        path = self.directory / name
        if path.is_symlink() or not path.is_file():
            return None
        _, limit = self.rules.save_bounds(self.info)
        with open(path, "rb") as stream:
            return stream.read(limit + 1)

    def _is_envelope(self, data: Optional[bytes]) -> bool:
        _, limit = self.rules.save_bounds(self.info)
        if data is None or len(data) > limit:
            return False
        try:
            document = json.loads(data)
        except ValueError:
            return False
        return isinstance(document, dict) and document.get("format") == self.format

    def _other_generation(self, data: Optional[bytes]) -> Optional[str]:
        try:
            document = json.loads(data) if data is not None else None
        except ValueError:
            return None
        found = document.get("format") if isinstance(document, dict) else None
        return found if found in SAVE_FORMATS.values() and found != self.format else None

    def restore(self, target: Path) -> Dict[str, Any]:
        """Give a session its copy of the current save, by the console's
        restore rules: nothing when there is none; the previous copy, marked,
        when the current one is missing or of the other naming generation and
        the previous one is a valid envelope; a save that does not parse is
        still handed to a single-object game, which reports it."""
        if not self.directory.is_dir():
            return {"present": False}
        data = self._read("save.json")
        other = self._other_generation(data)
        recovered = False
        if data is None or other:
            previous = self._read("save.json.prev")
            if not self._is_envelope(previous):
                if other:
                    return {"present": True, "copied": False, "error": {
                        "code": "save_other_generation",
                        "message": "La partida de la Game Card no corresponde a esta tarjeta"}}
                return {"present": False}
            data, recovered = previous, True
            document = json.loads(data)
            document["recovered_from"] = "save.json.prev"
            data = (json.dumps(document, ensure_ascii=False) + "\n").encode("utf-8")
        target.write_bytes(data)
        os.chmod(target, 0o600)
        return {"present": True, "copied": True, "recovered": recovered, "bytes": len(data)}

    # -- writing ---------------------------------------------------------
    def write(self, session: str, schema: int, payload: dict) -> Dict[str, Any]:
        """Write a save by the console's protocol: the new envelope under a
        temporary name, synced; the current one kept as save.json.prev (or
        set aside as damaged); one rename onto save.json; the directory
        synced. At every step the directory holds a loadable save."""
        envelope = {"format": self.format, "game": self.info["id"], "content_version": self.info["version"],
                    "schema": schema, "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "session": session, "insertion": self.insertion, "payload": payload}
        data = (json.dumps(envelope, ensure_ascii=False) + "\n").encode("utf-8")
        # The console measures what it is about to write against the bound it
        # reads with: a save it confirms is one it reads back whole.
        _, limit = self.rules.save_bounds(self.info)
        if len(data) > limit:
            return {"ok": False, "error": {"code": "save_too_large", "message": TOO_LARGE}}
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.directory / "save.json.tmp"
        current = self.directory / "save.json"
        try:
            if temporary.exists() or temporary.is_symlink():
                temporary.unlink()
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
            try:
                written = 0
                while written < len(data):
                    written += os.write(fd, data[written:])
                os.fsync(fd)
            finally:
                os.close(fd)
            if current.is_symlink() or current.exists():
                intact = current.is_file() and not current.is_symlink() and self._is_envelope(self._read("save.json"))
                keep = self.directory / ("save.json.prev" if intact else
                                         "save.json.damaged-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
                if current.is_file() and not current.is_symlink():
                    link = Path(str(keep) + ".tmp")
                    if link.exists() or link.is_symlink():
                        link.unlink()
                    os.link(current, link)
                    os.replace(link, keep)
                else:
                    os.replace(current, keep)
            os.replace(temporary, current)
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            try:
                temporary.unlink()
            except OSError:
                pass
            code = {errno.ENOSPC: "no_space", errno.EIO: "sync_failed"}.get(exc.errno, "write_failed")
            return {"ok": False, "error": {"code": code, "message": CARD_FAULTS[code], "detail": str(exc)}}
        return {"ok": True, "bytes": len(data)}


def _too_deep(line: bytes, limit: int = JSON_MAX_DEPTH) -> bool:
    """Whether the JSON in `line` nests arrays and objects deeper than `limit`."""
    depth, inside, escaped = 0, False, False
    for byte in line:
        if inside:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                inside = False
        elif byte == 0x22:
            inside = True
        elif byte in b"[{":
            depth += 1
            if depth > limit:
                return True
        elif byte in b"]}":
            depth -= 1
    return False


class _LineReader:
    """Newline-delimited JSON as the console's launcher reads it: a malformed
    line, one nested too deep or one that is not an object is skipped without
    an answer; a line over the limit, finished or not, is a protocol break
    (OverflowError)."""

    def __init__(self, limit: int):
        self.buffer = bytearray()
        self.limit = limit

    def feed(self, data: bytes) -> List[dict]:
        self.buffer.extend(data)
        messages: List[dict] = []
        while True:
            newline = self.buffer.find(b"\n")
            if newline < 0:
                break
            if newline > self.limit:
                self.buffer.clear()
                raise OverflowError("line over the limit")
            line = bytes(self.buffer[:newline])
            del self.buffer[:newline + 1]
            if _too_deep(line):
                continue
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict):
                messages.append(message)
        if len(self.buffer) > self.limit:
            self.buffer.clear()
            raise OverflowError("unfinished line over the limit")
        return messages


class SaveGate:
    """The session's save socket. One thread accepts connections and reads
    requests; each accepted save is written by a worker, and answered only if
    the session is still running when the card has answered."""

    def __init__(self, path: Path, card: SimCard, session: str, fail: Optional[Dict[str, int]] = None,
                 save_delay: float = 0.0, log: Callable[[str], None] = lambda line: None,
                 request_timeout: float = SAVE_REQUEST_TIMEOUT):
        self.path, self.card, self.session, self.log = Path(path), card, session, log
        self.request_timeout = request_timeout
        self.fail = dict(fail or {})        # code -> how many more saves fail with it (-1: all)
        self.save_delay = save_delay
        self.running = True                 # the session's state, as the launcher sees it
        self.saving = False
        self.saves: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener.bind(str(self.path))
        os.chmod(self.path, 0o600)
        self._listener.listen(8)
        self._listener.setblocking(False)
        self._thread = threading.Thread(target=self._serve, name="save-gate", daemon=True)
        self._thread.start()

    # -- the launcher's checks, in its order -------------------------------
    def _take_fault(self, codes) -> Optional[str]:
        with self._lock:
            for code in codes:
                left = self.fail.get(code, 0)
                if left:
                    if left > 0:
                        self.fail[code] = left - 1
                    return code
        return None

    def request(self, message: dict, reply: Callable[[dict], None]) -> None:
        def refuse(code: str, text: str) -> None:
            self.saves.append({"ok": False, "code": code})
            reply({"type": "saved", "ok": False, "error": {"code": code, "message": text}})
        if not self.running:
            return refuse("no_session", "No hay una sesión de juego en curso")
        fault = self._take_fault(LAUNCHER_FAULTS)
        if fault:
            return refuse(fault, LAUNCHER_FAULTS[fault])
        if message.get("type") != "save" or not isinstance(message.get("data"), dict):
            return refuse("save_invalid", "La petición de guardado no es válida")
        schema = message.get("schema")
        if not isinstance(schema, int) or isinstance(schema, bool) or schema < 1:
            return refuse("save_invalid", "La versión del esquema de guardado no es válida")
        # Measured as the card will hold it: compact, in UTF-8, every
        # character as itself however the line spelt it. Text UTF-8 cannot
        # carry (half of a surrogate pair, which JSON can spell) is no save.
        try:
            size = len(json.dumps(message["data"], separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        except UnicodeEncodeError:
            return refuse("save_invalid", "La partida contiene texto que no se puede guardar")
        if size > SAVE_MAX_BYTES:
            return refuse("save_too_large", TOO_LARGE)
        with self._lock:
            if self.saving:
                busy = True
            else:
                busy, self.saving = False, True
        if busy:
            return refuse("save_busy", "Ya hay un guardado en curso")

        def work() -> None:
            if self.save_delay:
                time.sleep(self.save_delay)
            fault = self._take_fault(CARD_FAULTS)
            if fault:
                outcome = {"ok": False, "error": {"code": fault, "message": CARD_FAULTS[fault]}}
            else:
                outcome = self.card.write(self.session, schema, message["data"])
            with self._lock:
                self.saving = False
            self.saves.append({"ok": bool(outcome.get("ok")), **({"bytes": outcome["bytes"]} if outcome.get("ok")
                                                                   else {"code": outcome["error"]["code"]})})
            if not self.running:
                self.log("save answered after the session ended; not delivered")
                return
            reply({"type": "saved", "ok": bool(outcome.get("ok")),
                   **({"bytes": outcome.get("bytes")} if outcome.get("ok") else {"error": outcome.get("error")})})
        threading.Thread(target=work, name="save", daemon=True).start()

    # -- the socket --------------------------------------------------------
    def _serve(self) -> None:
        selector = selectors.DefaultSelector()
        selector.register(self._listener, selectors.EVENT_READ, None)
        # Per connection: its reader until it has asked (then None), and the
        # time by which a connection that has asked nothing is closed.
        readers: Dict[socket.socket, Optional[_LineReader]] = {}
        deadlines: Dict[socket.socket, float] = {}

        def drop(connection: socket.socket) -> None:
            readers.pop(connection, None)
            deadlines.pop(connection, None)
            try:
                selector.unregister(connection)
            except (KeyError, ValueError):
                pass
            connection.close()

        def send(connection: socket.socket, message: dict) -> None:
            try:
                connection.setblocking(True)
                connection.settimeout(2.0)
                connection.sendall((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8", "replace"))
            except OSError:
                pass

        while not self._stop.is_set():
            for key, _ in selector.select(timeout=0.05):
                if key.fileobj is self._listener:
                    try:
                        connection, _ = self._listener.accept()
                    except OSError:
                        continue
                    if len(readers) >= SAVE_CLIENTS_MAX:
                        # Room is made by the connection silent for longest;
                        # one waiting for its answer stays.
                        silent = [held for held, reader in readers.items() if reader is not None]
                        if not silent:
                            connection.close()
                            continue
                        drop(min(silent, key=lambda held: deadlines[held]))
                    connection.setblocking(False)
                    readers[connection] = _LineReader(SAVE_LINE_MAX)
                    deadlines[connection] = time.monotonic() + self.request_timeout
                    selector.register(connection, selectors.EVENT_READ, None)
                    continue
                connection = key.fileobj
                reader = readers.get(connection)
                try:
                    data = connection.recv(65536)
                    # Anything after the request line is read and dropped.
                    requests = reader.feed(data) if (data and reader) else []
                except OverflowError:
                    # No save needs a line that long: the game is told, then dropped.
                    self.saves.append({"ok": False, "code": "save_too_large"})
                    send(connection, {"type": "saved", "ok": False,
                                      "error": {"code": "save_too_large", "message": TOO_LARGE}})
                    drop(connection)
                    continue
                except OSError:
                    data, requests = b"", []
                if not data:
                    drop(connection)
                    continue
                for message in requests[:1]:            # one request per connection
                    readers[connection] = None
                    self.request(message, lambda answer, c=connection: send(c, answer))
            now = time.monotonic()
            for connection in [held for held, reader in readers.items()
                               if reader is not None and now > deadlines[held]]:
                drop(connection)
        for connection in list(readers):
            drop(connection)
        selector.close()

    def end_session(self) -> None:
        """The game has stopped: no new saves, pending answers undelivered."""
        self.running = False

    def close(self) -> None:
        self.running = False
        self._stop.set()
        self._thread.join(timeout=2.0)
        self._listener.close()
        try:
            self.path.unlink()
        except OSError:
            pass


def _result_of(returncode: int) -> Dict[str, Any]:
    """A session's result in the console's words (docs/runtime.md)."""
    if returncode == 0:
        return {"result": "exited", "exit_code": 0}
    if returncode > 0:
        return {"result": "failed", "exit_code": returncode}
    try:
        name = signal.Signals(-returncode).name
    except ValueError:
        name = str(-returncode)
    return {"result": "crashed", "signal": name}


def run_session(entry: Path, card: SimCard, profile: str, content: Optional[Path] = None,
                display: Optional[str] = "1920x1080", extra_env: Optional[Dict[str, str]] = None,
                console_env: bool = False, stop_after: Optional[float] = None, timeout: Optional[float] = None,
                fail: Optional[Dict[str, int]] = None, save_delay: float = 0.0,
                root: Optional[Path] = None, log: Callable[[str], None] = print) -> Dict[str, Any]:
    """Run one session of `entry` against `card` and return its record.

    `stop_after` ends the game as the console does (SIGTERM, then SIGKILL
    after the grace period); `timeout` is the simulator's own bound for a run
    nobody stops, after which the game is ended the same way and the record
    says so."""
    if profile not in PROFILE_ENVIRONMENT:
        raise MdkError("E-PROFILE", f"Unknown runtime profile {profile!r}.",
                       "The console knows: " + ", ".join(PROFILE_ENVIRONMENT) + ".")
    entry = Path(entry)
    if not entry.is_file():
        raise MdkError("E-ENTRY", f"The entry {entry} does not exist.", "Build the project first (`mdk build`).")
    # A short path: a UNIX socket's name is limited to about 100 bytes.
    holder = tempfile.mkdtemp(prefix="mun-", dir=str(root) if root else None)
    session = "s" + uuid.uuid4().hex[:10]
    dest = Path(holder) / session
    work = dest / "work"
    work.mkdir(parents=True)
    record: Dict[str, Any] = {"session": session, "profile": profile, "card": card.info["id"]}
    gate = None
    try:
        game = dest / "game"
        shutil.copyfile(entry, game)
        os.chmod(game, 0o555)                       # the console's copy is read-only
        record["restore"] = card.restore(work / "save.json")
        facts = {"CARD_ID": card.info["id"], "CONTENT_VERSION": card.info["version"], "SESSION": session,
                 "RUNTIME_PROFILE": profile, "SAVE_FILE": str(work / "save.json"), "SAVE_SOCKET": str(dest / "save.sock")}
        if content is not None:
            facts["CONTENT_DIR"] = str(Path(content).resolve())
        if display and profile == "linux-arm64-gl-v0":
            facts["DISPLAY_MODE"] = display
        environment = {"MUN_" + name: value for name, value in facts.items()}
        environment["HOME"] = str(work)
        environment["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        if console_env:
            environment.update(PROFILE_ENVIRONMENT[profile])
        environment.update(extra_env or {})
        gate = SaveGate(dest / "save.sock", card, session, fail=fail, save_delay=save_delay,
                        log=lambda line: log(f"session {session}: {line}"))
        started = time.monotonic()
        try:
            process = subprocess.Popen([str(game)], cwd=str(work), env=environment, stdin=subprocess.DEVNULL)
        except OSError as exc:
            if exc.errno == errno.ENOEXEC:
                raise MdkError("E-HOST-ARCH", f"{entry} cannot run on this computer.",
                               "A MUN game is an AArch64 Linux program; run it in the SDK's environment "
                               "with `mdk sim --container`.", str(exc))
            raise MdkError("E-START", f"The game could not be started: {exc}.")
        stop: Dict[str, Any] = {"requested": False}
        deadline = None if stop_after is None else started + stop_after
        limit = None if timeout is None else started + timeout
        while process.poll() is None:
            now = time.monotonic()
            reason = "stop" if deadline is not None and now >= deadline else \
                     "timeout" if limit is not None and now >= limit else None
            if reason is None:
                time.sleep(0.02)
                continue
            stop = {"requested": True, "reason": reason, "signal": "SIGTERM"}
            asked = time.monotonic()
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=STOP_GRACE)
                stop["seconds_to_end"] = round(time.monotonic() - asked, 3)
                stop["killed"] = False
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                stop["killed"] = True
        gate.end_session()
        record.update(_result_of(process.returncode))
        record["seconds"] = round(time.monotonic() - started, 3)
        record["stop"] = stop
        time.sleep(0.05)                            # let a write in flight finish, as the console accounts for it
        record["saves"] = list(gate.saves)
    finally:
        if gate is not None:
            gate.close()
        shutil.rmtree(holder, ignore_errors=True)
    return record


def parse_fail(values: Optional[List[str]]) -> Dict[str, int]:
    """`CODE` (every save) or `CODE:N` (the next N saves)."""
    plan: Dict[str, int] = {}
    for value in values or []:
        code, _, count = value.partition(":")
        if code not in FAULTS:
            raise MdkError("E-FAULT", f"Unknown save fault {code!r}.", "Known: " + ", ".join(sorted(FAULTS)) + ".")
        try:
            plan[code] = int(count) if count else -1
        except ValueError:
            raise MdkError("E-FAULT", f"{value!r}: the count after ':' must be a number.")
    return plan


def describe(record: Dict[str, Any]) -> str:
    restore = record.get("restore", {})
    if not restore.get("present"):
        restored = "no save on the card (a new game)"
    elif not restore.get("copied"):
        restored = "the card's save was not used: " + restore.get("error", {}).get("code", "?")
    else:
        restored = "save restored" + (" from the previous copy" if restore.get("recovered") else "")
    if record["result"] == "exited":
        ending = "exited (0)"
    elif record["result"] == "failed":
        ending = f"failed (exit code {record['exit_code']})"
    else:
        ending = f"crashed ({record['signal']})"
    stop = record.get("stop", {})
    if stop.get("requested"):
        ending += ("; stopped with SIGTERM, " +
                   ("KILLED after the 3 s grace period" if stop.get("killed")
                    else f"ended in {stop.get('seconds_to_end')} s"))
    saves = record.get("saves", [])
    good = sum(1 for save in saves if save["ok"])
    refused = [save["code"] for save in saves if not save["ok"]]
    lines = [f"session {record['session']}: {restored}",
             f"  game {ending} after {record['seconds']} s",
             f"  saves: {good} written" + (f", {len(refused)} refused ({', '.join(refused)})" if refused else "")]
    return "\n".join(lines)
