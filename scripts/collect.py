"""يجمع لقطة من سلاسل الأوبشن (Cboe المجاني، وyfinance احتياطاً)
ويضيفها إلى data/options_history.csv. تعيد اللقطةُ في اليوم نفسه كتابةَ سابقتها."""
import csv, datetime as dt, json, os, re, urllib.request
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "options_history.csv")
COLS = ["date", "ticker", "type", "strike", "expiry", "volume", "oi", "price", "spot", "delta", "ts", "final"]
MAX_DAYS, KEEP_DAYS = 45, 30     # أبعد انتهاء يُجمع، ومدة الاحتفاظ بالتاريخ
MIN_VOL, MIN_PREM = 100, 10000
MIN_DTE = 4                      # نستبعد العقود التي تنتهي خلال 3 أيام أو أقل (0DTE تضخّم القراءة)   # لا نخزّن إلا العقود المؤثرة لإبقاء الملف صغيراً
OCC = re.compile(r"^(.+?)(\d{6})([CP])(\d{8})$")

def session_date(now=None):
    """تاريخ الجلسة التي تخصها البيانات (بتوقيت نيويورك).
    قبل افتتاح 9:30 ص تكون بيانات Cboe ما زالت لإغلاق اليوم السابق، فننسبها إليه،
    ونتخطى السبت والأحد. هكذا لا يُخطئ التاريخ حتى لو تأخر التشغيل المجدول ساعات."""
    now = now or dt.datetime.now(ZoneInfo("America/New_York"))
    d = now.date()
    if now.time() < dt.time(9, 30):
        d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


TODAY = session_date()


def from_cboe(sym):
    url = f"https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)["data"]
    opts = data["options"]
    spot = data.get("current_price") or data.get("close")
    rows = []
    for o in opts:
        m = OCC.match(o.get("option", ""))
        if not m:
            continue
        exp = dt.datetime.strptime(m.group(2), "%y%m%d").date()
        price = o.get("last_trade_price") or 0
        rows.append((sym, m.group(3), int(m.group(4)) / 1000, exp,
                     int(o.get("volume") or 0), int(o.get("open_interest") or 0), float(price),
                     o.get("delta")))
    return rows, spot


def from_yf(sym):
    import yfinance as yf
    tk, rows = yf.Ticker(sym), []
    try:
        spot = float(tk.fast_info["last_price"])
    except Exception:
        spot = None
    for e in tk.options:
        exp = dt.date.fromisoformat(e)
        if (exp - TODAY).days > MAX_DAYS:
            continue
        ch = tk.option_chain(e)
        for typ, df in (("C", ch.calls), ("P", ch.puts)):
            for r in df.itertuples():
                v = 0 if r.volume != r.volume else int(r.volume)
                oi = 0 if r.openInterest != r.openInterest else int(r.openInterest)
                rows.append((sym, typ, float(r.strike), exp, v, oi, float(r.lastPrice), None))
    return rows, spot


def get_rows(sym):
    for fn in (from_cboe, from_yf):
        try:
            rows, spot = fn(sym)
            if rows:
                nd = sum(1 for r in rows if r[7] not in (None, ""))
                print(sym, fn.__name__, len(rows), "| سعر السهم:", spot, "| عقود بدلتا:", nd)
                return rows, spot
        except Exception as ex:
            print(sym, fn.__name__, "فشل:", ex)
    return [], None


def snapshot_info():
    """وقت اللقطة بتوقيت نيويورك، وعلامة final=1 إذا كانت بعد إغلاق السوق (مع هامش التأخير 15 دقيقة)
    أو قبل الافتتاح أو في عطلة نهاية الأسبوع، أي أنها تمثل إغلاق الجلسة لا منتصفها."""
    now = dt.datetime.now(ZoneInfo("America/New_York"))
    in_session = now.weekday() < 5 and dt.time(9, 30) <= now.time() < dt.time(16, 15)
    return now.isoformat(timespec="minutes"), 0 if in_session else 1


def main():
    ts, final = snapshot_info()
    syms = [s.strip().upper() for s in open(os.path.join(ROOT, "tickers.txt")) if s.strip()]
    new = []
    for s in syms:
        rows, spot = get_rows(s)
        spot = round(float(spot), 2) if spot else ""
        for t, typ, k, exp, v, oi, p, dl in rows:
            if MIN_DTE <= (exp - TODAY).days <= MAX_DAYS and v >= MIN_VOL and v * p * 100 >= MIN_PREM:
                dl = round(float(dl), 4) if dl not in (None, "") else ""
                new.append([TODAY.isoformat(), t, typ, k, exp.isoformat(), v, oi, p, spot, dl, ts, final])
    if not new:
        print("لا بيانات جديدة؛ لن يُعدَّل الملف.")
        return
    old = []
    if os.path.exists(OUT):
        with open(OUT, newline="") as f:
            old = list(csv.reader(f))[1:]
        old = [r + [""] * (len(COLS) - len(r)) for r in old]   # صفوف قديمة بلا spot/delta/ts/final
    cutoff = (TODAY - dt.timedelta(days=KEEP_DAYS)).isoformat()
    keep = [r for r in old if r[0] != TODAY.isoformat() and r[0] >= cutoff]
    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        w.writerows(sorted(keep + new))
    print("تم حفظ", len(new), "عقداً لتاريخ", TODAY, "| وقت اللقطة:", ts, "| لقطة نهائية:", bool(final))


if __name__ == "__main__":
    main()
