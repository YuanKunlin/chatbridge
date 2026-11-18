import sqlite3, sys
for p in sys.argv[1:]:
    c = sqlite3.connect(p)
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.close()
    print("checkpoint", p)
