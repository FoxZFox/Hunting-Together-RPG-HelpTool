"""Pick which server of the game to join.

Uses Roblox's public server list (no login needed), the same list the RoomSpoof
Chrome extension reads. Joining one is a roblox:// link with its gameInstanceId.

It also keeps a respawn schedule: every server where we saw a boss's respawn sign, or
killed the boss ourselves, has a known time its boss comes back. A server whose boss
respawns about when we would get there is picked before any other.
"""
import json
import random
import time
import urllib.error
import urllib.request

API = 'https://games.roblox.com/v1/games/{place}/servers/Public?sortOrder=Asc&excludeFullGames=true&limit=100'


class Servers:
    def __init__(self, place_id, cooldown_s=720, pages=2):
        self.place = place_id
        self.cooldown = cooldown_s
        self.pages = pages
        self.visited = {}               # server id -> when we went there
        self.cache = (0.0, [])          # (time, servers): the API answers 429 when asked too often
        self.current = None             # the server we are in, when we picked it
        self.respawn = {}               # (server id, boss) -> when that boss respawns (epoch seconds)
        self.travel = 30.0              # seconds from leaving a server to watching the boss in the next

    def fetch(self):
        if time.time() - self.cache[0] < 45:
            return self.cache[1]
        servers, cursor = [], None
        for _ in range(self.pages):
            url = API.format(place=self.place) + (f'&cursor={cursor}' if cursor else '')
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            for wait in (5, 15, None):
                try:
                    data = json.load(urllib.request.urlopen(req, timeout=15))
                    break
                except urllib.error.HTTPError as e:
                    if e.code != 429 or wait is None:
                        raise
                    time.sleep(wait)
            servers += data.get('data', [])
            cursor = data.get('nextPageCursor')
            if not cursor:
                break
        self.cache = (time.time(), servers)
        return servers

    def pick(self, strategy='fewest', skip=(0, 0)):
        """The server to go to next, or None: one of the emptiest ('fewest'), the busiest ('most')
        or any ('random', like Roblox's own Quick Join). Leaves a free slot or two so the join does
        not race someone else into a full server.

        skip=(10, 20) passes over a random 10-20 of the very emptiest first: other people hopping
        for bosses go straight for those, so their bosses are usually already dead.
        """
        now = time.time()
        fresh = [s for s in self.fetch()
                 if s.get('playing', 0) <= s.get('maxPlayers', 0) - 2
                 and now - self.visited.get(s['id'], 0) > self.cooldown]
        if not fresh:
            return None
        if strategy == 'random':
            best = random.choice(fresh)
        elif strategy == 'most':
            best = max(fresh, key=lambda s: (s.get('playing', 0), -(s.get('ping') or 999)))
        else:
            fresh.sort(key=lambda s: (s.get('playing', 0), s.get('ping') or 999))
            best = fresh[min(len(fresh) - 1, random.randint(*skip))]
        self.visited[best['id']] = now
        self.current = best['id']
        return best

    def note_respawn(self, boss, seconds):
        """The boss of the server we are in respawns in `seconds`."""
        if self.current:
            self.respawn[(self.current, boss)] = time.time() + seconds

    def note_travel(self, seconds):
        if 10 < seconds < 120:
            self.travel = 0.7 * self.travel + 0.3 * seconds

    def schedule(self, boss, late=15):
        """Seconds until each known respawn of `boss` (soonest first; a little past counts)."""
        now = time.time()
        for key in [k for k, at in self.respawn.items() if at < now - 120]:
            del self.respawn[key]
        return sorted(at - now for (sid, b), at in self.respawn.items() if b == boss and at > now - late)

    def due(self, boss, early=15, late=10):
        """(server, seconds until its boss respawns) for a known server whose `boss` respawns about
        when we would be watching it: up to `early` s after we get there (we wait for it) or `late`
        s before (a camper may have it already, but often not). None when there is none."""
        now = time.time()
        best = None
        for srv in self.fetch():
            at = self.respawn.get((srv['id'], boss))
            if at is None or srv['id'] == self.current or srv.get('playing', 0) >= srv.get('maxPlayers', 0):
                continue
            gap = at - (now + self.travel)          # > 0: we are there before it spawns
            if -late <= gap <= early and (best is None or abs(gap - 3) < abs(best[1] - 3)):
                best = (srv, gap)
        if best:
            self.visited[best[0]['id']] = now
            self.current = best[0]['id']
            return best[0], best[1] + self.travel
        return None

    def join_url(self, server):
        return f'roblox://placeId={self.place}&gameInstanceId={server["id"]}'
