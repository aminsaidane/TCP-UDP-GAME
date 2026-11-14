# Demo Guide — 4 PCs

How to set up the server and the three client PCs, run the live demo, and show the TCP and UDP traffic in Wireshark.

| PC | Runs | Who |
|---|---|---|
| **PC 1** | the server (`python -m server.main --stats`) + Wireshark | presenter |
| **PC 2, 3, 4** | one client each (`python -m client.main`) | the three players |

---

## 1. One-time setup (every PC)

1. **Python 3.10 or newer.** On Windows, tick **"Add python.exe to PATH"** in the installer. Check with:
   ```bash
   python --version          # on Windows "py --version" also works
   ```
2. **Get the code** (or download the ZIP from GitHub and unzip it):
   ```bash
   git clone https://github.com/aminsaidane/tcp-udp-game.git
   cd tcp-udp-game
   ```
3. **Install pygame** on the three client PCs (the server needs nothing else):
   ```bash
   pip install -r requirements.txt
   ```
4. **Optional sanity check** (takes about a minute):
   ```bash
   python -m unittest
   ```

---

## 2. The network

All four PCs must be on the **same network**.

| Option | Notes |
|---|---|
| Lab network / same Wi-Fi | Works if the network lets devices talk to each other. Some university Wi-Fi networks block this ("client isolation"). |
| **Phone hotspot** (recommended fallback) | Reliable: one phone, four PCs connected to it. Bring one. |
| Different networks | Not possible with plain LAN addresses: see the bonus (Tailscale). |

On **Windows**, set the network to **Private** on every PC: *Settings → Network & Internet → Wi-Fi → (your network) → Network profile type → Private*. On a *Public* network, Windows blocks incoming connections.

---

## 3. Firewall (server PC)

The server must accept **TCP 5000** (game) and **UDP 5001–5002** (heartbeats, hover, timer, discovery).

**Windows** — open **PowerShell as administrator** on the server PC and run:

```powershell
netsh advfirewall firewall add rule name="Pablo TCP" dir=in action=allow protocol=TCP localport=5000 profile=private,domain
netsh advfirewall firewall add rule name="Pablo UDP" dir=in action=allow protocol=UDP localport=5001-5002 profile=private,domain
```

To remove them after the demo:

```powershell
netsh advfirewall firewall delete rule name="Pablo TCP"
netsh advfirewall firewall delete rule name="Pablo UDP"
```

The first time Python uses the network, Windows may also show a pop-up: click **Allow** with **Private networks** ticked, on every PC (the client PCs receive UDP hover/timer packets too).

**Linux** (ufw): `sudo ufw allow 5000/tcp && sudo ufw allow 5001:5002/udp`
**macOS**: click **Allow** when asked whether Python may accept incoming connections.

---

## 4. Starting

**PC 1 (server):**

```bash
python -m server.main --stats
```

```
[10:02:11] TCP server listening on 0.0.0.0:5000
[10:02:11] UDP game port 5001, LAN discovery port 5002
[10:02:11] Players connect to: 192.168.43.17  (or click Find in the client)
```

`--stats` prints a traffic summary every 10 seconds:

```
[traffic] TCP in: 21 msgs / 789 B (login 4, list_rooms 4, register 3, peek 3) | TCP out: 76 msgs / 18743 B (state 21, event 14, room_update 10, rooms 7) | UDP in: 24 msgs / 1688 B (heartbeat 20, hello 4) | ...
```

**PC 2, 3, 4 (players)** — first check the network, then start the game:

```bash
python -m client.check            # finds the server and tests TCP and UDP
python -m client.main
```

`client.check` should end with **All good**. If not, it says what to fix (see *Troubleshooting*). In the game, click **Find** (LAN discovery) or type the server IP, then **Register** the first time.

---

## 5. Demo script (about 10 minutes)

Each line shows one required feature. Players: **A** (PC 2), **B** (PC 3), **C** (PC 4).

| # | Who | Do | Feature shown | Point to make |
|---|---|---|---|---|
| 1 | presenter | Start the server, show the log line with its IP | client–server architecture | 1 server, 3 clients, 4 PCs |
| 2 | A | Click **Find** | UDP **broadcast** | The client doesn't know the server's address: only UDP can ask the whole network |
| 3 | A, B, C | **Register** (A also tries a wrong password) | registration & login | Passwords stored as salted hashes; the server log shows each login |
| 4 | A | **Create room** (4 players) | lobby & rooms | B and C see the room appear without refreshing (pushed over TCP) |
| 5 | B, C | **Join**; C leaves and joins again | joining / leaving | Room updates reach everyone; the Start button enables at 3 players |
| 6 | A | **Start game** | game start | Only the host can start |
| 7 | all | Peek at 2 cards, play a few turns: draw, swap, a Jack or Queen if one comes | real-time actions over TCP | Private cards (drawn, peeked) are sent only to their owner |
| 8 | B, C | Move the mouse over cards | UDP hover | Coloured outlines on the other screens: frequent, disposable data |
| 9 | all | After a throw, **cut** (try one wrong cut) | the cut race | Server log shows millisecond timestamps: arrival order decides; the countdown says "timer: UDP" |
| 10 | C | **Pull C's network** (Wi-Fi off) | disconnection | After ~5 s without UDP heartbeats the server drops C; the game goes on for A and B |
| 11 | C | Wi-Fi back on, log in again | rejoin | C watches the rest of the round and is dealt in at the next one |
| 12 | someone | Call **Pablo** | scores & results | Revealed hands, −5 / +50, totals; host clicks Next round |
| 13 | presenter | Show `--stats` and Wireshark | TCP vs UDP | Game moves on TCP 5000; heartbeats/hover/timer on UDP 5001 |

**To show the match end quickly**, a match normally lasts several rounds. A wrong Pablo is +50, so two wrong Pablos by the same player end the match fast.

---

## 6. Wireshark (on the server PC)

1. Start Wireshark **before** the clients connect, and capture on the **Wi-Fi / Ethernet adapter** the players use (not "Loopback").
2. Use these display filters:

| Filter | Shows |
|---|---|
| `tcp.port == 5000 \|\| udp.port == 5001 \|\| udp.port == 5002` | all game traffic |
| `tcp.port == 5000` | logins, rooms, every game move (TCP) |
| `udp.port == 5001` | heartbeats (1 per second per player), hover, timer ticks (UDP) |
| `udp.port == 5002` | discovery: the broadcast to `255.255.255.255` and the server's reply |
| `tcp.flags.syn == 1 && tcp.port == 5000` | the 3-way handshake when a client connects |
| `tcp.flags.fin == 1 \|\| tcp.flags.reset == 1` | connections closing (logout, crash) |
| `tcp.analysis.retransmission` | TCP resending lost data (the reliability UDP doesn't have) |

3. Things worth showing:
   - **Right-click a TCP 5000 packet → Follow → TCP Stream.** The whole conversation appears as readable JSON lines: `{"type":"login",...}`, `{"type":"draw"}`, `{"type":"cut","slot":1}`... One message per line, which is how the program splits the TCP byte stream into messages.
   - **A UDP 5001 packet**: the payload is a single JSON object with a `token` and a `seq` number: no connection, so each packet says who sent it and in which order.
   - **The discovery broadcast** on UDP 5002: destination `255.255.255.255`, and the reply coming back directly to the client.
   - **The disconnection in step 10**: the heartbeats from C stop, and about 5 seconds later the server closes C's TCP connection (FIN).
   - **Statistics → Conversations**: one TCP conversation per player, plus their UDP flows.
   - **Statistics → I/O Graphs** with the filter `udp.port == 5001`: a steady line of heartbeats, with bursts of timer ticks during cut windows.
4. **Save the capture** (*File → Save As*, `.pcapng`) in case the live network misbehaves on the day. `.pcapng` files are in `.gitignore`.

Note: passwords travel in plain text inside the TCP login message (visible in Wireshark). That's fine for a LAN demo, and a good question to anticipate: a real game would use TLS.

---

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `client.check`: TCP *timed out* | firewall on the server PC, or networks that don't talk to each other | firewall rules (section 3); network set to Private; try the phone hotspot |
| `client.check`: TCP *refused* | server not running, or a wrong IP/port | start the server; use the IP it prints |
| **Find** finds nothing, but typing the IP works | the network blocks broadcasts | type the IP (the game works the same) |
| `client.check`: UDP fails, TCP works | UDP 5001–5002 blocked | firewall rule for UDP; meanwhile the game still works (timer shows "local", no hover) |
| A player is dropped every few seconds | their UDP heartbeats are lost but other UDP works | check the Wi-Fi signal; the timeout is `HEARTBEAT_TIMEOUT` in `common/config.py` |
| `OSError: address already in use` on the server | a previous server is still running | close it (or restart the terminal) |
| `ModuleNotFoundError: pygame` | pygame not installed on that PC | `pip install -r requirements.txt` |
| `ModuleNotFoundError: common` | run from the wrong folder | `cd` into `tcp-udp-game` first, use `python -m ...` |
| "this account is already logged in" | the old connection is still open | wait 5 s (heartbeat timeout) or restart that client |

---

## 8. Checklist (the day before)

- [ ] Same commit on all 4 PCs (`git pull`), `pip install -r requirements.txt` done on the clients
- [ ] Phone hotspot ready as a backup network, charged
- [ ] Firewall rules added on the server PC; network set to **Private** on all PCs
- [ ] `python -m client.check` says **All good** on PC 2, 3 and 4
- [ ] Accounts registered for the 3 players (the server keeps them in `users.json`)
- [ ] One full rehearsal of the demo script, including pulling the Wi-Fi of one client
- [ ] Wireshark installed on the server PC, filters tested, a backup capture saved
- [ ] Laptops plugged in; screen sleep turned off
