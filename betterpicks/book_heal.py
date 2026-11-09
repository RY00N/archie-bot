# Nov 9: book.json keeps turning up 0 bytes on the project mount. If it doesn't parse, put back the newest good backup.
import glob, json, os, shutil, sys
BP = os.path.dirname(os.path.abspath(__file__)); B = os.path.join(BP, "book.json")
def ok(p):
    try: return bool(json.load(open(p)).get("parlays")) and os.path.getsize(p) > 1e6
    except Exception: return False
if ok(B): sys.exit(0)
for p in sorted(glob.glob(os.path.join(BP, "backups/book-*.json")), key=os.path.getmtime, reverse=True):
    if ok(p):
        shutil.copyfile(p, B + ".heal.tmp"); os.replace(B + ".heal.tmp", B)
        print(f"BOOK RESTORED from {os.path.basename(p)} (book.json was bad)"); sys.exit(0)
print("BOOK BAD and no good backup"); sys.exit(1)
