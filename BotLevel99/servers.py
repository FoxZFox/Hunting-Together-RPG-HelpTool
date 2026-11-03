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
PAGE_S = 22.5       # the list answers 429 past ~3 requests a minute: a list is used 22.5 s per page it cost
MAX_PAGES = 6
WANTED = 30         # joinable servers (see joinable) worth having to pick from
STALE_S = 300       # while Roblox refuses (429), a list this old still beats a random server


class Servers:
    def __init__(self, place_id, cooldown_s=720, pages=2):
        self.place = place_id
        self.cooldown = cooldown_s
        self.pages = pages
        self.min_players = 0            # servers with fewer players are only a fallback
        self.visited = {}               # server id -> when we went there
        self.cache = (0.0, [], 0, None)  # (when read, servers, pages it cost, cursor of the next page)
        self.retry_at = 0.0             # after a refusal, ask again no sooner than this
        self.current = None             # the server we are in, when we picked it
        self.respawn = {}               # (server id, boss) -> when that boss respawns (epoch seconds)
        self.travel = 30.0              # seconds from leaving a server to watching the boss in the next

    def _page(self, cursor=None):
        url = API.format(place=self.place) + (f'&cursor={cursor}' if cursor else '')
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        for wait in (5, 15, None):
            try:
                return json.load(urllib.request.urlopen(req, timeout=15))
            except urllib.error.HTTPError as e:
                if e.code != 429 or wait is None:
                    self.retry_at = time.time() + 30
                    raise
                time.sleep(wait)

    def joinable(self, servers, busy=True):
        """Servers we could go to now: a free slot or two (so the join does not race someone
        else into a full server), not visited within the cooldown, and with min_players or
        more when `busy`."""
        now = time.time()
        return [s for s in servers
                if s.get('playing', 0) <= s.get('maxPlayers', 0) - 2
                and now - self.visited.get(s['id'], 0) > self.cooldown
                and (not busy or s.get('playing', 0) >= self.min_players)]

    def fetch(self):
        """Public servers from the emptiest up: `pages` pages, then one more at a time (up to
        MAX_PAGES) while fewer than WANTED are joinable. On 2026-11-04 the first 100 had 2-4
        players, the next 100 4-7 and the next 7-10 (13 a server)."""
        at, servers, cost, cursor = self.cache
        now = time.time()
        if now - at >= cost * PAGE_S:                   # too old: read it again from the top
            try:
                if now < self.retry_at:
                    raise OSError('Roblox refused the server list a moment ago')
                data = self._page()
            except OSError:
                if servers and now - at < STALE_S:
                    return servers
                raise
            at, servers, cost, cursor = time.time(), data.get('data', []), 1, data.get('nextPageCursor')
        while (cursor and cost < MAX_PAGES and time.time() >= self.retry_at
               and (cost < self.pages or len(self.joinable(servers)) < WANTED)):
            try:
                data = self._page(cursor)
            except OSError:
                break                                   # make do with what we have
            servers = servers + data.get('data', [])
            cost, cursor = cost + 1, data.get('nextPageCursor')
        self.cache = (at, servers, max(cost, 2), cursor)
        return servers

    def pick(self, strategy='fewest', skip=(0, 0)):
        """The server to go to next, or None: one of the emptiest ('fewest'), the busiest ('most')
        or any ('random', like Roblox's own Quick Join) of the joinable ones. skip=(0, 9) picks
        at random among the 10 emptiest.

        Servers with 2-5 players are mostly boss campers, some exploiting, whose bosses are
        always dead; at 6+ players the user found people running bots and no campers. When
        every server with min_players has been visited lately, the busiest of the rest come
        closest.
        """
        now = time.time()
        servers = self.fetch()
        fresh = self.joinable(servers)
        if not fresh:
            fresh = sorted(self.joinable(servers, busy=False), key=lambda s: -s.get('playing', 0))[:10]
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

    def due(self, boss, early=15, late=2):
        """(server, seconds until its boss respawns) for a known server whose `boss` respawns just
        after we would be watching it (up to `early` s; we wait for it there). Arriving later is not
        worth it: a server with someone camping its boss had it dead within 3 s of the spawn."""
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
