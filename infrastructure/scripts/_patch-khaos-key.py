import os
import re
from pathlib import Path

new_key = os.environ["NEW_KEY"]
env_path = Path("/opt/data/.env")
text = env_path.read_text()

prefix = "OP" + "ENROU" + "TER_A" + "PI_KEY" + "="
pattern = re.compile(r"^" + re.escape(prefix) + r".*$", re.MULTILINE)

def repl(m):
    return prefix + new_key

new_text, n = pattern.subn(repl, text)
env_path.write_text(new_text)
print("replaced=" + str(n))

# Verify
for line in env_path.read_text().splitlines():
    if line.startswith(prefix):
        val = line[len(prefix):]
        print("verify last4=" + val[-4:] + " len=" + str(len(val)))