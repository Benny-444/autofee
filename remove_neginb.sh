#!/bin/bash
#
# remove_neginb.sh
# -----------------------------------------------------------------------------
# Remove one or more channels from autofee negative-inbound management and zero
# out their negative inbound fee everywhere it lives.
#
# For each target SCID it will:
#   1. Edit the include lists in autofee_neginb_wrapper.py:
#        - always add the SCID to EXCLUDE_CHAN_IDS
#        - remove it from CHAN_IDS only if that leaves >=1 entry
#          (if it is the sole entry, keep it AND add to EXCLUDE -- "double-list"
#           -- so CHAN_IDS never goes empty and flips the wrapper to "all channels")
#   2. Remove the SCID's entry from neginb_fees.json
#   3. Set inbound_fee_ppm = 0 in the SCID's section of dynamic_charge.ini, then
#      run charge-lnd on the current INI to push inbound = 0 into LND.
#
# All file edits are backed up (timestamped) and require explicit confirmation.
#
# Usage:
#   ./remove_neginb.sh <scid> [<scid> ...]
#   ./remove_neginb.sh --dry-run <scid> [<scid> ...]
#
# Example:
#   ./remove_neginb.sh 996507179527241729
#   ./remove_neginb.sh 996507179527241729 901883557103861761
# -----------------------------------------------------------------------------

# Embed the Python in a temp file and run that (rather than piping it in via
# stdin) so the script's stdin stays attached to the terminal and the
# confirmation prompt works on any shell.
PYSRC="$(mktemp)" || { echo "ERROR: could not create temp file" >&2; exit 1; }
trap 'rm -f "$PYSRC"' EXIT
cat > "$PYSRC" <<'PYEOF'
import sys, os, re, ast, json, shutil, shlex, subprocess, configparser
from datetime import datetime

HOME       = os.path.expanduser('~')
AUTOFEE    = os.path.join(HOME, 'autofee')
WRAPPER    = os.path.join(AUTOFEE, 'autofee_neginb_wrapper.py')
NEGINB_JSON= os.path.join(AUTOFEE, 'neginb_fees.json')
CHARGE_INI = os.path.join(AUTOFEE, 'dynamic_charge.ini')
CHARGE_DIR = os.path.join(AUTOFEE, 'charge-lnd')
MACAROON   = os.path.join(AUTOFEE, 'charge-lnd.macaroon')

USAGE = (
    "Usage: remove_neginb.sh [--dry-run] <scid> [<scid> ...]\n"
    "  Removes channel(s) from autofee negative-inbound management and resets\n"
    "  their inbound fee to 0 in LND, dynamic_charge.ini and neginb_fees.json.\n"
    "  --dry-run / -n : show the plan and exit without changing anything.\n"
    "  Example: remove_neginb.sh 996507179527241729\n"
)

# ----------------------------------------------------------------------------
# Pure helpers (validated by test_logic.py)
# ----------------------------------------------------------------------------
def scid_to_section(scid):
    n = int(scid)
    block = n >> 40
    tx = (n >> 16) & 0xFFFFFF
    out = n & 0xFFFF
    return f"autofee-{block}x{tx}x{out}"

def read_list(src, name):
    m = re.search(rf'^[ \t]*{re.escape(name)}\s*=\s*(\[[^\]]*\])', src, re.MULTILINE)
    if not m:
        raise ValueError(f"Could not find '{name} = [...]' in {WRAPPER}")
    return list(ast.literal_eval(m.group(1)))

def write_list(src, name, items):
    pat = re.compile(
        rf'^(?P<head>[ \t]*{re.escape(name)}\s*=\s*)(?P<list>\[[^\]]*\])(?P<rest>.*)$',
        re.MULTILINE)
    matches = list(pat.finditer(src))
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one '{name}' assignment, found {len(matches)}")
    m = matches[0]
    literal = "[" + ", ".join(repr(str(x)) for x in items) + "]"
    new_line = m.group('head') + literal + m.group('rest')
    return src[:m.start()] + new_line + src[m.end():]

def apply_rule(chan_ids, exclude_ids, scids):
    """Return (new_chan, new_exclude, actions, kept_sole).
    Rule: always add target to EXCLUDE; remove from CHAN_IDS only if that leaves
    >=1 entry; never let a previously non-empty CHAN_IDS become empty."""
    chan_str = [str(x) for x in chan_ids]
    excl_str = [str(x) for x in exclude_ids]
    targets  = [str(s) for s in scids]
    chan_was_nonempty = len(chan_ids) > 0
    targets_in_chan = [t for t in targets if t in chan_str]
    new_chan = [x for x in chan_ids if str(x) not in targets]
    kept_sole = None
    if chan_was_nonempty and len(new_chan) == 0 and targets_in_chan:
        kept_sole = targets_in_chan[0]
        new_chan = [kept_sole]
    new_exclude = list(exclude_ids)
    new_excl_str = [str(x) for x in new_exclude]
    for t in targets:
        if t not in new_excl_str:
            new_exclude.append(t); new_excl_str.append(t)
    actions = {}
    for t in targets:
        if t == kept_sole:
            chan_act = 'kept_sole'
        elif t in targets_in_chan:
            chan_act = 'removed'
        else:
            chan_act = 'absent'
        excl_act = 'already' if t in excl_str else 'added'
        actions[t] = (chan_act, excl_act)
    return new_chan, new_exclude, actions, kept_sole

# ----------------------------------------------------------------------------
# I/O helpers
# ----------------------------------------------------------------------------
def atomic_write(path, text):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        f.write(text)
    os.replace(tmp, path)

def backup(path):
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    dst = f"{path}.backup.{ts}"
    shutil.copy2(path, dst)
    return dst

def run_lncli_json(args, timeout=30):
    out = subprocess.check_output(['lncli'] + args, stderr=subprocess.DEVNULL, timeout=timeout)
    return json.loads(out.decode())

def lookup_channels(scids):
    """Best-effort scid -> {alias, active, found}. Never raises."""
    info = {str(s): {'alias': 'Unknown', 'active': None, 'found': False} for s in scids}
    try:
        chans = run_lncli_json(['listchannels'])['channels']
        by_scid = {str(c.get('scid')): c for c in chans}
        for s in scids:
            c = by_scid.get(str(s))
            if not c:
                continue
            info[str(s)]['found'] = True
            info[str(s)]['active'] = bool(c.get('active', False))
            rp = c.get('remote_pubkey')
            if rp:
                try:
                    ni = run_lncli_json(['getnodeinfo', rp])
                    info[str(s)]['alias'] = (ni.get('node', {}) or {}).get('alias') or 'Unknown'
                except Exception:
                    pass
    except Exception:
        pass
    return info

def confirm(prompt):
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled - no changes made.")
        sys.exit(0)

def fmt_list(items):
    if not items:
        return "[]"
    return "[" + ", ".join(repr(str(x)) for x in items) + "]"

# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    args = sys.argv[1:]
    dry_run = False
    scids = []
    for a in args:
        if a in ('--dry-run', '-n'):
            dry_run = True
        elif a in ('-h', '--help'):
            print(USAGE); sys.exit(0)
        elif a.startswith('-'):
            print(f"Unknown option: {a}\n"); print(USAGE); sys.exit(1)
        else:
            scids.append(a)

    if not scids:
        print(USAGE); sys.exit(1)
    for s in scids:
        if not s.isdigit():
            print(f"ERROR: '{s}' is not a valid SCID (expected a positive decimal number).")
            sys.exit(1)
    # de-dupe, preserve order
    seen = set()
    scids = [s for s in scids if not (s in seen or seen.add(s))]

    # --- load wrapper + parse lists (fatal if missing) ---
    if not os.path.exists(WRAPPER):
        print(f"ERROR: wrapper not found: {WRAPPER}"); sys.exit(1)
    with open(WRAPPER) as f:
        wrapper_src = f.read()
    try:
        chan_ids    = read_list(wrapper_src, 'CHAN_IDS')
        exclude_ids = read_list(wrapper_src, 'EXCLUDE_CHAN_IDS')
    except ValueError as e:
        print(f"ERROR: {e}"); sys.exit(1)

    new_chan, new_exclude, list_actions, kept_sole = apply_rule(chan_ids, exclude_ids, scids)
    wrapper_changes = (new_chan != chan_ids) or (new_exclude != exclude_ids)

    # --- load json (optional) ---
    json_data = None
    json_present = os.path.exists(NEGINB_JSON)
    if json_present:
        try:
            with open(NEGINB_JSON) as f:
                json_data = json.load(f)
        except Exception as e:
            print(f"ERROR: could not read {NEGINB_JSON}: {e}"); sys.exit(1)
    json_actions = {}   # scid -> 'remove' / 'absent'
    for s in scids:
        json_actions[s] = 'remove' if (json_data is not None and s in json_data) else 'absent'
    json_changes = any(v == 'remove' for v in json_actions.values())

    # --- load ini (optional) ---
    ini_present = os.path.exists(CHARGE_INI)
    cfg = configparser.ConfigParser()
    if ini_present:
        try:
            cfg.read(CHARGE_INI)
        except Exception as e:
            print(f"ERROR: could not parse {CHARGE_INI}: {e}"); sys.exit(1)
    ini_actions = {}   # scid -> (section, status, will_change)
    for s in scids:
        sec = scid_to_section(s)
        if not ini_present:
            ini_actions[s] = (sec, "INI missing - cannot push reset", False)
            continue
        if cfg.has_section(sec):
            old = cfg.get(sec, 'inbound_fee_ppm', fallback=None)
            if old is None:
                ini_actions[s] = (sec, "add inbound_fee_ppm = 0 (none set)", True)
            elif old.strip() == '0':
                ini_actions[s] = (sec, "already 0 (no INI change)", False)
            else:
                ini_actions[s] = (sec, f"set inbound_fee_ppm 0 (was {old.strip()})", True)
        else:
            ini_actions[s] = (sec, "no section - will create minimal section, inbound 0", True)
    ini_changes = any(v[2] for v in ini_actions.values())

    # --- aliases (best effort) ---
    chan_info = lookup_channels(scids)

    # ------------------------------------------------------------------
    # Plan display
    # ------------------------------------------------------------------
    line = "=" * 63
    print(line)
    print("  remove_neginb.sh  -  remove channel(s) from neg-inbound management")
    print(line)
    print(f"Targets: {len(scids)} channel(s)")
    print()
    empty_note = "  (empty -> all channels managed)" if not chan_ids else ""
    print("Current neginb wrapper lists:")
    print(f"  CHAN_IDS         = {fmt_list(chan_ids)}{empty_note}")
    print(f"  EXCLUDE_CHAN_IDS = {fmt_list(exclude_ids)}")
    print()
    print("Planned changes per channel:")
    for s in scids:
        ci = chan_info[s]
        if ci['active'] is True:
            status = "active"
        elif ci['active'] is False:
            status = "INACTIVE"
        elif not ci['found']:
            status = "not found in listchannels"
        else:
            status = "status unknown"
        chan_act, excl_act = list_actions[s]
        sec, ini_status, _ = ini_actions[s]
        print()
        print(f"  SCID:   {s}")
        print(f"  Alias:  {ci['alias']}   ({status})")
        if chan_act == 'kept_sole':
            print(f"  - CHAN_IDS         : KEPT as sole entry (double-listed) *")
        elif chan_act == 'removed':
            print(f"  - CHAN_IDS         : removed")
        else:
            print(f"  - CHAN_IDS         : not in list (no change)")
        print(f"  - EXCLUDE_CHAN_IDS : {'added' if excl_act=='added' else 'already present (no change)'}")
        print(f"  - neginb_fees.json : {'entry present -> will remove' if json_actions[s]=='remove' else 'no entry (no change)'}")
        print(f"  - dynamic_charge.ini [{sec}]: {ini_status}")

    if kept_sole is not None:
        print()
        print("  * SOLE-ENTRY NOTE:")
        print(f"    {kept_sole} is the only channel in CHAN_IDS. Removing it would empty")
        print( "    CHAN_IDS, which flips the wrapper to managing ALL channels. To avoid")
        print( "    that, it is kept in CHAN_IDS AND added to EXCLUDE_CHAN_IDS. Net effect:")
        print( "    negative inbound is disabled for it and no other channel is affected.")
        print( "    (Do not hand-remove it from CHAN_IDS later without re-checking this.)")

    print()
    print("Resulting neginb wrapper lists:")
    print(f"  CHAN_IDS         = {fmt_list(new_chan)}")
    print(f"  EXCLUDE_CHAN_IDS = {fmt_list(new_exclude)}")
    print()

    files_to_write = []
    if wrapper_changes: files_to_write.append(os.path.basename(WRAPPER))
    if json_changes:    files_to_write.append(os.path.basename(NEGINB_JSON))
    if ini_changes:     files_to_write.append(os.path.basename(CHARGE_INI))
    if files_to_write:
        print("Files to be modified (a timestamped backup is taken first):")
        for fn in files_to_write:
            print(f"  - {fn}")
    else:
        print("No file edits needed (lists/json/ini already in the target state).")
    print()

    if ini_present:
        print("After edits, charge-lnd runs on the current INI to push inbound = 0 into LND.")
        print("  Note: charge-lnd re-applies the WHOLE current INI; this is a no-op for")
        print("  every channel except the targets above.")
    else:
        print("WARNING: dynamic_charge.ini not found - cannot push inbound=0 into LND now.")
        print("  Wrapper/json edits will still apply. Reset LND inbound on the next")
        print("  pipeline run or with a manual charge-lnd / lncli once the INI exists.")
    print(line)

    if dry_run:
        print("DRY RUN - no changes made.")
        sys.exit(0)

    if not (files_to_write or ini_present):
        print("Nothing to do.")
        sys.exit(0)

    # ------------------------------------------------------------------
    # Confirm
    # ------------------------------------------------------------------
    ans = confirm("Proceed? (type 'yes' to confirm): ")
    if ans != 'yes':
        print("Cancelled - no changes made.")
        sys.exit(0)
    print()

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------
    # 1) wrapper lists
    if wrapper_changes:
        b = backup(WRAPPER); print(f"  backup: {b}")
        new_src = wrapper_src
        new_src = write_list(new_src, 'CHAN_IDS', new_chan)
        new_src = write_list(new_src, 'EXCLUDE_CHAN_IDS', new_exclude)
        atomic_write(WRAPPER, new_src)
        print(f"  updated {os.path.basename(WRAPPER)}: "
              f"CHAN_IDS={fmt_list(new_chan)}  EXCLUDE_CHAN_IDS={fmt_list(new_exclude)}")
    else:
        print(f"  {os.path.basename(WRAPPER)}: no change")

    # 2) neginb_fees.json
    if json_changes:
        b = backup(NEGINB_JSON); print(f"  backup: {b}")
        removed = []
        for s in scids:
            if s in json_data:
                json_data.pop(s); removed.append(s)
        atomic_write(NEGINB_JSON, json.dumps(json_data))
        print(f"  updated {os.path.basename(NEGINB_JSON)}: removed {', '.join(removed)}")
    else:
        print(f"  {os.path.basename(NEGINB_JSON)}: no change")

    # 3) dynamic_charge.ini
    if ini_present and ini_changes:
        b = backup(CHARGE_INI); print(f"  backup: {b}")
        for s in scids:
            sec = scid_to_section(s)
            if not cfg.has_section(sec):
                cfg.add_section(sec)
                cfg.set(sec, 'chan.id', str(s))
                cfg.set(sec, 'strategy', 'static')
            cfg.set(sec, 'inbound_fee_ppm', '0')
        tmp = CHARGE_INI + '.tmp'
        with open(tmp, 'w') as f:
            cfg.write(f)
        os.replace(tmp, CHARGE_INI)
        print(f"  updated {os.path.basename(CHARGE_INI)}: inbound_fee_ppm = 0 for target section(s)")
    elif ini_present:
        print(f"  {os.path.basename(CHARGE_INI)}: inbound already 0 (no edit)")

    # 4) charge-lnd (enforce the reset in LND)
    if ini_present:
        print()
        print("  running charge-lnd to apply inbound = 0 ...")
        cmd = (f"cd {shlex.quote(CHARGE_DIR)} && source venv/bin/activate && "
               f"charge-lnd --macaroon {shlex.quote(MACAROON)} "
               f"-c {shlex.quote(CHARGE_INI)} -v")
        rc = subprocess.run(['bash', '-c', cmd]).returncode
        print()
        if rc == 0:
            print("  charge-lnd completed successfully.")
        else:
            print(f"  WARNING: charge-lnd exited with code {rc}.")
            print( "  The wrapper/json/INI edits were applied, but LND inbound may NOT have")
            print( "  been reset. Re-run charge-lnd manually BEFORE the next pipeline run")
            print( "  (which regenerates the INI without an inbound line for excluded channels):")
            print(f"    cd {CHARGE_DIR} && source venv/bin/activate && \\")
            print(f"    charge-lnd --macaroon {MACAROON} -c {CHARGE_INI} -v")
            sys.exit(1)

    print()
    print("Done.")

if __name__ == "__main__":
    main()
PYEOF

python3 "$PYSRC" "$@"
