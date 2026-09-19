#!/usr/bin/env python3
"""Generate GitHub profile stat cards (dark & light SVG) for the profile README.

Self-hosted replacement for github-readme-stats / top-langs / activity-graph /
trophy vercel instances (which kept 502-ing through GitHub camo). Runs in
GitHub Actions daily with GITHUB_TOKEN; also works locally unauthenticated
(activity chart falls back to the public events API without a token).

Output: stats-{dark,light}.svg, langs-{dark,light}.svg, activity-{dark,light}.svg
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import ssl

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE  # sandbox/CI proxies may re-sign TLS

PINK = "#F724A9"
THEMES = {
    "dark":  {"bg": "#0D1117", "border": "#21283B", "title": "#F724A9", "text": "#C9D1D9", "muted": "#8B949E", "bar_track": "#161B22", "axis": "#8B949E"},
    "light": {"bg": "#FFFFFF", "border": "#D0D7DE", "title": "#F724A9", "text": "#24292F", "muted": "#57606A", "bar_track": "#F0F1F3", "axis": "#57606A"},
}
FONT = "'Segoe UI','PingFang SC','Microsoft YaHei',Helvetica,Arial,sans-serif"


def api(path, token=None):
    req = urllib.request.Request("https://api.github.com" + path, headers={
        "User-Agent": "profile-stats-gen", "Accept": "application/vnd.github+json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as r:
                return json.loads(r.read().decode()), r.headers
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def graphql(query, variables, token):
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request("https://api.github.com/graphql", data=body, headers={
        "User-Agent": "profile-stats-gen", "Authorization": "Bearer " + token,
        "Content-Type": "application/json"})
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as r:
        return json.loads(r.read().decode())


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt(n):
    return f"{n:,}"


# ---------------------------------------------------------------- data ----
def collect(username, token):
    user, _ = api(f"/users/{username}", token)
    repos, _ = api(f"/users/{username}/repos?per_page=100&sort=updated", token)
    repos = [r for r in repos if not r.get("fork")]
    stars = sum(r["stargazers_count"] for r in repos)
    forks = sum(r["forks_count"] for r in repos)

    commits = 0
    lang_bytes = {}
    for r in repos:
        try:
            _, hdr = api(f"/repos/{r['full_name']}/commits?per_page=1", token)
            link = hdr.get("Link") or ""
            if 'rel="last"' in link:
                commits += int(link.split('page=')[-1].split('>')[0])
            else:
                commits += 1
        except Exception:
            pass
        try:
            langs, _ = api(f"/repos/{r['full_name']}/languages", token)
            for k, v in langs.items():
                lang_bytes[k] = lang_bytes.get(k, 0) + v
        except Exception:
            pass

    prs = issues = 0
    try:
        q = urllib.parse.quote(f"author:{username} type:pr")
        prs = api(f"/search/issues?q={q}&per_page=1", token)[0].get("total_count", 0)
        q = urllib.parse.quote(f"author:{username} type:issue")
        issues = api(f"/search/issues?q={q}&per_page=1", token)[0].get("total_count", 0)
    except Exception:
        pass

    days = []
    total_contrib = 0
    if token:
        try:
            res = graphql(
                "query($u:String!){ user(login:$u){ contributionsCollection { "
                "contributionCalendar { totalContributions weeks { contributionDays "
                "{ date contributionCount } } } } } }", {"u": username}, token)
            cal = res["data"]["user"]["contributionsCollection"]["contributionCalendar"]
            total_contrib = cal.get("totalContributions", 0)
            for w in cal["weeks"]:
                days += [(d["date"], d["contributionCount"]) for d in w["contributionDays"]]
        except Exception as e:
            print(f"[warn] graphql contributions failed: {e}", file=sys.stderr)
    if not days:  # unauthenticated fallback: public events, ~90 days
        per_day = {}
        try:
            for page in (1, 2, 3):
                evs, _ = api(f"/users/{username}/events/public?per_page=100&page={page}", token)
                if not evs:
                    break
                for ev in evs:
                    per_day[ev["created_at"][:10]] = per_day.get(ev["created_at"][:10], 0) + 1
        except Exception:
            pass
        days = sorted(per_day.items())
        total_contrib = sum(c for _, c in days)

    return {
        "username": username, "followers": user["followers"], "repos": len(repos),
        "stars": stars, "forks": forks, "commits": commits, "prs": prs, "issues": issues,
        "langs": sorted(lang_bytes.items(), key=lambda kv: -kv[1]), "days": days,
        "total_contrib": total_contrib,
    }


LANG_COLORS = {
    "Python": "#3572A5", "JavaScript": "#F1E05A", "TypeScript": "#3178C6", "C": "#555555",
    "C++": "#F34B7D", "C#": "#178600", "Rust": "#DEA584", "Go": "#00ADD8", "Shell": "#89E051",
    "HTML": "#E34C26", "CSS": "#563D7C", "Vue": "#41B883", "Java": "#B07219", "Kotlin": "#A97BFF",
    "Dart": "#00B4AB", "Ruby": "#701516", "PHP": "#4F5D95", "Lua": "#000080", "Shell": "#89E051",
    "Jupyter Notebook": "#DA5B0B", "Makefile": "#427819", "Dockerfile": "#384D54", "Nix": "#7E7EFF",
    "Vue": "#41B883", "SCSS": "#C6538C", "Assembly": "#6E4C13", "Roff": "#ECDEBE", "Blade": "#F7523F",
}


# ----------------------------------------------------------------- dog ----
def dog_svg(x, y, scale, flip=False, animated=True):
    """Front-facing white line puppy (maltese style): round head, floppy ears,
    blush cheeks, w-shape mouth. Drawn in a 100x100 box, then placed."""
    f = f' transform="translate({x},{y}) scale({-scale if flip else scale},{scale})"'
    tail_anim = eye_anim = ""
    if animated:
        tail_anim = ('<animateTransform attributeName="transform" type="rotate" '
                     'values="-6 73 68;14 73 68;-6 73 68" dur="1.5s" repeatCount="indefinite"/>')
        blink = ('<animate attributeName="ry" values="2.3;2.3;2.3;0.3;2.3;2.3" '
                 'keyTimes="0;0.44;0.5;0.55;0.6;1" dur="4.8s" repeatCount="indefinite"/>')
        eye_anim = blink
    stroke = 'stroke="#1F2328" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"'
    return f"""<g{f}>
 <path d="M73 68 C81 66 84 58 77 52" fill="none" {stroke}>{tail_anim}</path>
 <path d="M30 50 C22 58 20 70 26 79 C32 86 68 86 74 79 C80 70 78 58 70 50 C60 45 40 45 30 50 Z" fill="#FFFDF8" {stroke}/>
 <path d="M43 62 L43 81 M57 62 L57 81" fill="none" {stroke}/>
 <path d="M39.5 81 C41 84.5 45 84.5 46.5 81 M53.5 81 C55 84.5 59 84.5 60.5 81" fill="none" {stroke} stroke-width="2.2"/>
 <path d="M38 19 C27 21 21 36 26 50 C29 57 36 56 37 47 C38 38 38 28 38 19 Z" fill="#FFFDF8" {stroke}/>
 <path d="M62 19 C73 21 79 36 74 50 C71 57 64 56 63 47 C62 38 62 28 62 19 Z" fill="#FFFDF8" {stroke}/>
 <circle cx="50" cy="34" r="20" fill="#FFFDF8" {stroke}/>
 <path d="M40 17 C41 12 46 11 47 15 C48 11 53 11 54 15 C55 12 59 12 60 17" fill="none" {stroke} stroke-width="2"/>
 <ellipse cx="43" cy="32" rx="2.3" ry="2.3" fill="#1F2328">{eye_anim}</ellipse>
 <ellipse cx="57" cy="32" rx="2.3" ry="2.3" fill="#1F2328">{eye_anim}</ellipse>
 <circle cx="38" cy="40" r="3.6" fill="#F7A8D4" opacity="0.55"/>
 <circle cx="62" cy="40" r="3.6" fill="#F7A8D4" opacity="0.55"/>
 <ellipse cx="50" cy="39.5" rx="2.3" ry="1.8" fill="#1F2328"/>
 <path d="M50 41.5 C50 44.5 47.5 45.5 45.5 44.5 M50 41.5 C50 44.5 52.5 45.5 54.5 44.5" fill="none" stroke="#1F2328" stroke-width="1.5" stroke-linecap="round"/>
</g>"""


# ---------------------------------------------------------------- cards ---
def card_header(t, w, h, title):
    return (f'<rect x="0.75" y="0.75" width="{w - 1.5}" height="{h - 1.5}" rx="12" '
            f'fill="{t["bg"]}" stroke="{t["border"]}" stroke-width="1.5"/>'
            f'<text x="25" y="36" font-family="{FONT}" font-size="16" font-weight="600" '
            f'fill="{t["title"]}">{esc(title)}</text>')


def svg(w, h, body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" role="img">' + body + "</svg>")


def stats_card(d, theme):
    t, w, h = THEMES[theme], 500, 210
    rows = [
        ("★", "Total Stars", d["stars"]), ("⟳", "Total Commits", d["commits"]),
        ("⇅", "Pull Requests", d["prs"]), ("◎", "Issues", d["issues"]),
        ("◉", "Followers", d["followers"]), ("▣", "Own Repos", d["repos"]),
    ]
    y0, dy = 68, 26
    lines = []
    for i, (icon, name, val) in enumerate(rows):
        y = y0 + i * dy
        lines.append(f'<text x="30" y="{y}" font-family="{FONT}" font-size="14.5" fill="{t["text"]}">'
                     f'<tspan fill="{PINK}" font-weight="700">{icon}</tspan>  {esc(name)}</text>')
        lines.append(f'<text x="245" y="{y}" font-family="{FONT}" font-size="15" font-weight="700" '
                     f'text-anchor="end" fill="{PINK}">{fmt(val)}</text>')
    body = card_header(t, w, h, f"{d['username']}'s GitHub Stats")
    body += "".join(lines)
    body += dog_svg(360, 42, 1.35)
    body += (f'<text x="430" y="196" font-family="{FONT}" font-size="11" text-anchor="middle" '
             f'fill="{t["muted"]}">~ woof ~</text>')
    return svg(w, h, body)


def langs_card(d, theme):
    t, w, h = THEMES[theme], 430, 210
    langs = d["langs"][:6]
    total = sum(v for _, v in langs) or 1
    body = card_header(t, w, h, "Most Used Languages")
    x0, bw, y = 25, w - 50, 66
    body += f"<rect x='{x0}' y='{y}' width='{bw}' height='12' rx='6' fill='{t['bar_track']}'/>"
    cx = x0
    for name, vb in langs:
        frac = vb / total
        seg = bw * frac
        color = LANG_COLORS.get(name, PINK)
        body += (f"<rect x='{cx}' y='{y}' width='{max(seg - 1.5, 1)}' height='12' rx='6' fill='{color}'>"
                 f"<title>{esc(name)} {frac * 100:.1f}%</title></rect>")
        cx += seg
    col_x = [x0, x0 + 195]
    for i, (name, vb) in enumerate(langs):
        col, row = divmod(i, 3)
        lx = col_x[col]
        ly = 108 + row * 30
        color = LANG_COLORS.get(name, PINK)
        pct = vb / total * 100
        body += (f"<circle cx='{lx + 6}' cy='{ly - 4}' r='5' fill='{color}'/>"
                 f"<text x='{lx + 20}' y='{ly}' font-family=\"{FONT}\" font-size='13.5' fill='{t['text']}'>"
                 f"{esc(name)}</text>"
                 f"<text x='{lx + 178}' y='{ly}' font-family=\"{FONT}\" font-size='13' font-weight='600' "
                 f'text-anchor="end" fill="{t["muted"]}">{pct:.1f}%</text>')
    return svg(w, h, body)


def activity_card(d, theme):
    t, w, h = THEMES[theme], 560, 185
    days = d["days"][-365:]
    body = card_header(t, w, h, "Contribution Activity — last year")
    if len(days) < 8:
        body += (f"<text x='{w / 2}' y='{h / 2}' font-family=\"{FONT}\" font-size='14' "
                 f"text-anchor='middle' fill='{t['muted']}'>no public activity yet</text>")
        return svg(w, h, body)
    # bucket by week
    weeks = [days[i:i + 7] for i in range(0, len(days), 7)]
    vals = [sum(c for _, c in wk) for wk in weeks]
    total = d["total_contrib"]
    x0, x1, y0, y1 = 40, w - 30, h - 42, 58
    mx = max(max(vals), 1)
    n = len(vals)
    pts = []
    for i, v in enumerate(vals):
        x = x0 + (x1 - x0) * i / max(n - 1, 1)
        y = y0 - (y0 - y1) * (v / mx)
        pts.append((x, y))
    # smooth path (catmull-rom -> bezier)
    path = f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"
    for i in range(1, len(pts)):
        x0p, y0p = pts[i - 1]
        x1p, y1p = pts[i]
        mx_ = (x0p + x1p) / 2
        path += f" C{mx_:.1f},{y0p:.1f} {mx_:.1f},{y1p:.1f} {x1p:.1f},{y1p:.1f}"
    area = path + f" L{x1:.1f},{y0:.1f} L{x0:.1f},{y0:.1f} Z"
    body += (f"<path d='{area}' fill='{PINK}' opacity='0.12'/>"
             f"<path d='{path}' fill='none' stroke='{PINK}' stroke-width='2.4' "
             f"stroke-linecap='round' stroke-linejoin='round'/>")
    lastx, lasty = pts[-1]
    body += f"<circle cx='{lastx:.1f}' cy='{lasty:.1f}' r='4' fill='{PINK}'/>"
    # month ticks
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    start = days[0][0]
    try:
        si = int(start[5:7]) - 1
    except Exception:
        si = 0
    for k in range(0, n, max(n // 12, 1)):
        x = x0 + (x1 - x0) * k / max(n - 1, 1)
        m = months[(si + k // 4) % 12]
        body += (f"<text x='{x:.1f}' y='{y0 + 18}' font-family=\"{FONT}\" font-size='10.5' "
                 f"text-anchor='middle' fill='{t['axis']}'>{m}</text>")
    body += (f"<text x='{x1}' y='40' font-family=\"{FONT}\" font-size='13' font-weight='700' "
             f"text-anchor='end' fill='{t['text']}'>{fmt(total)} "
             f"<tspan fill='{t['muted']}' font-weight='400'>contributions</tspan></text>")
    return svg(w, h, body)


# ---------------------------------------------------------------- main ----
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--username", default="HiChat-fog")
    ap.add_argument("--outdir", default="dist")
    ap.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)
    d = collect(args.username, args.token)
    print(f"[data] stars={d['stars']} commits={d['commits']} prs={d['prs']} issues={d['issues']} "
          f"followers={d['followers']} repos={d['repos']} langs={len(d['langs'])} "
          f"contrib_days={len(d['days'])} total_contrib={d['total_contrib']}")
    for theme in THEMES:
        for name, fn in (("stats", stats_card), ("langs", langs_card), ("activity", activity_card)):
            s = fn(d, theme)
            p = os.path.join(args.outdir, f"{name}-{theme}.svg")
            with open(p, "w", encoding="utf-8") as f:
                f.write(s)
            print(f"[ok] {p} ({len(s)}B)")


if __name__ == "__main__":
    main()
