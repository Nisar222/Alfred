# Handoff: Recording Investigation

**Date:** 2026-08-13  
**Status:** Recordings not linking to calls (audio players missing in UI)  
**Priority:** High - customer waiting

---

## Summary

Recordings were working fine 48 hours ago but stopped. 3CX IS recording calls (confirmed by user test), but Alfred is not linking them. All other issues have been fixed and deployed.

---

## ✅ Already Fixed & Deployed

1. **Ghost Calls** - Complete ghost call monitoring system deployed:
   - `dispatcher.py` - Fixed commit error handling
   - `ghost_monitor.py` - New background service (checks every 5min)
   - `main.py` - Integrated into lifespan
   - `/health/ghost-calls` endpoint added
   - **Status:** Live on VPS, protecting campaigns

2. **UI Fixes** - All committed (commit d12256c):
   - Fixed `formatCallLogTime` undefined error
   - Changed default filter to "All"
   - Added campaign filter dropdown
   - Mobile scrolling improvements
   - Better empty states

3. **Settings** - Form binding fixed with `settingsFormBound` flag

4. **3CX Permissions** - Call Control API enabled and working

---

## ❌ Current Issue: Recordings Not Linking

### What We Know

1. **3CX is recording calls** (user confirmed)
2. **Recordings worked 48 hours ago** (then stopped)
3. **No 3CX config changes** (user confirmed)
4. **RecordingSync is running** (verified in deployment)
5. **Code changes made:**
   - `recordings.py` - Improved phone number matching
   - `recording_sync.py` - No changes
   - `main.py` - Added recording sync to lifespan

### Hypothesis

The sync is either:
1. Silently failing (catching `ThreeCXError` at line 160 in `recordings.py`)
2. Not finding matches (improved matching logic might have broken something)
3. Not connecting to 3CX API
4. Database issue

---

## Next Steps - Immediate Diagnosis

### 1. Check Recording Sync Status

```bash
cd ~/apps/alfred
docker compose logs api --tail=200 | grep -i record
```

**Look for:**
- Is sync running?
- Any errors?
- How many recordings linked?

### 2. Check Database

```bash
# Count recordings
docker compose exec db psql -U alfred -d alfred -c "SELECT COUNT(*) FROM recordings;"

# Check recent calls
docker compose exec db psql -U alfred -d alfred -c "
SELECT 
  id, 
  phone, 
  completed_at,
  (SELECT COUNT(*) FROM recordings WHERE call_id = calls.id) as has_recording
FROM calls 
WHERE status = 'completed' 
ORDER BY completed_at DESC 
LIMIT 10;
"
```

**Expected:** Should see some recordings, but recent calls probably have 0

### 3. Test 3CX API Connection

```bash
docker compose exec api python3 << 'EOF'
from app.config import get_settings
from app.threecx import ThreeCXClient

settings = get_settings()
client = ThreeCXClient(settings)
try:
    recordings = client.list_xapi_recordings()
    print(f"Found {len(recordings)} recordings in 3CX")
    if recordings:
        print(f"Latest recording ID: {recordings[0].get('Id')}")
        print(f"  From: {recordings[0].get('FromCallerNumber')}")
        print(f"  To: {recordings[0].get('ToCallerNumber')}")
        print(f"  Start: {recordings[0].get('StartTime')}")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")
finally:
    client.close()
EOF
```

**Expected:** Should list 3CX recordings successfully

### 4. Test Matching Logic

```bash
docker compose exec api python3 << 'EOF'
from app.config import get_settings
from app.database import SessionLocal
from app.threecx import ThreeCXClient
from app.recordings import sync_threecx_recordings

settings = get_settings()
client = ThreeCXClient(settings)
with SessionLocal() as db:
    try:
        linked = sync_threecx_recordings(db, client)
        print(f"Successfully linked {linked} recordings")
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    finally:
        client.close()
EOF
```

**Expected:** Should link recordings or show specific error

---

## Key Files

### Backend Files
- `backend/app/recordings.py` - Matching logic (lines 77-100: `best_matching_call`)
- `backend/app/recording_sync.py` - Background worker
- `backend/app/models.py` - Recording model (line 248: `recording_available` property)
- `backend/app/main.py` - Lifespan integration

### Frontend Files
- `backend/app/web/app.js` - Line 44: Audio player rendering
  ```javascript
  ${call.recording_available ? 
    `<audio controls preload="none" src="/calls/${call.id}/recording"></audio>` : 
    `<p class="recording-placeholder">Recording unavailable</p>`}
  ```

---

## Technical Context

### How Recording Sync Works

1. **RecordingSync** (daemon thread) runs every 30 seconds
2. Calls `sync_threecx_recordings_safe(db, settings)`
3. Fetches recordings from 3CX XAPI: `client.list_xapi_recordings()`
4. For each 3CX recording:
   - Extracts phone numbers from metadata
   - Finds matching Alfred call (within 15min window)
   - Creates `Recording` row linking to `Call`
5. Frontend checks `call.recording_available` property
6. Audio streams from `/calls/{id}/recording` endpoint

### Matching Logic

Phone numbers are normalized to last 10 digits:
```python
def phone_key(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    return digits[-10:] if len(digits) >= 10 else digits
```

Checks these 3CX fields:
- `FromCallerNumber`
- `ToCallerNumber` 
- `FromDisplayName`
- `ToDisplayName`

Matches calls within 15-minute window of recording start time.

---

## Recent Changes (Last 48 Hours)

### Commit d12256c: "Fix UI issues and improve recording matching"
- **recordings.py:** Added `recording_phone_keys()` to check all 4 fields (was only checking `FromCallerNumber`)
- **Test added:** `test_recordings.py` for outbound call matching

**Potential Issue:** The improved matching might have a bug, OR it exposed an existing issue.

---

## Likely Root Causes (In Order)

1. **Silent API failure** - `ThreeCXError` being caught and ignored (line 160)
2. **Matching logic bug** - New multi-field matching has edge case
3. **Phone format mismatch** - 3CX changed number format in recordings
4. **Timing issue** - Recording appears in 3CX before call completes in Alfred
5. **Database constraint** - Unique constraint preventing links

---

## SSH Issue Note

**Previous agent had SSH connection issues** - shell error prevented running commands directly. If you encounter the same, use the other agent in "VPS SSH connection" window or have user run commands.

Error message: `ssh:1: no matches found: db.refresh(call) that comes after client.close`

---

## Success Criteria

1. Run diagnostics above
2. Identify why sync is failing
3. Fix the issue
4. Verify: Make test call → wait 5 min → audio player appears in UI
5. Commit and deploy fix
6. Inform user recordings are working

---

## Contact Info

- **VPS:** `ssh nisar@165.154.217.39`
- **Project:** `/Users/nisarkhan/Documents/dev2/Alfred`
- **Docker:** `cd ~/apps/alfred && docker compose`
- **Repo:** `https://github.com/Nisar222/Alfred.git`

---

## User Context

- Customer is waiting to run campaigns
- System must be stable and reliable
- User is technical but prefers agent does the work
- Ghost call issue was successfully resolved - this is the last blocking issue

**Good luck! The diagnostics above should reveal the issue quickly.** 🎯
