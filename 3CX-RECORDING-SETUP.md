# 3CX Recording Configuration for Alfred

## Problem
Alfred calls aren't showing audio players in the UI because 3CX is not recording Route Point calls.

## What's Working
- ✅ Alfred's recording sync is running
- ✅ Recording matching logic fixed (matches all relevant phone numbers)
- ✅ `/calls/{id}/recording` endpoint streams from 3CX

## What's Missing
- ❌ 3CX is not configured to record Route Point calls
- Route Points are the type of call Alfred uses for outbound campaigns

## Solution: Configure 3CX to Record Route Point Calls

### Step 1: Access 3CX Management Console
1. Log into 3CX web interface as administrator
2. Navigate to **Settings** or **System Settings**

### Step 2: Configure Call Recording
Look for recording settings under one of these locations (varies by 3CX version):

**Option A - System-wide Recording:**
1. Go to **Settings** → **Call Recording**
2. Enable recording for:
   - ☑ External calls
   - ☑ Internal calls
   - ☑ **Route Point calls** (THIS IS KEY!)

**Option B - Route Point Specific:**
1. Go to **Route Points** in left menu
2. For each Route Point (or the one Alfred uses):
   - Edit the Route Point
   - Find recording settings
   - Enable **Record calls** checkbox

**Option C - Extension-based Recording:**
1. If Route Point calls appear as extension calls:
   - Go to **Extensions**
   - Find the extension used for Route Points
   - Enable recording for that extension

### Step 3: Check Recording Storage
1. Verify recording storage is configured:
   - Go to **Settings** → **Storage**
   - Ensure there's enough disk space
   - Check recording retention settings

### Step 4: Test
1. Make a test call through Alfred
2. Wait for call to complete
3. Check 3CX → **Call Log** → verify recording exists
4. Wait ~2-5 minutes for Alfred's RecordingSync to run
5. Check Alfred UI → Call Log → audio player should appear

## Verification Checklist

After configuration:
- [ ] Make test call through Alfred
- [ ] Check 3CX call log - recording exists
- [ ] Wait 5 minutes for sync
- [ ] Check Alfred UI - audio player appears
- [ ] Click play - audio streams correctly

## Troubleshooting

**If recording appears in 3CX but not in Alfred:**
- Check Alfred logs: `docker compose logs api | grep -i record`
- Check sync is running: `curl http://localhost:8000/health` (should show recording sync active)
- Verify call phone numbers match between Alfred and 3CX

**If recording doesn't appear in 3CX:**
- Recording not enabled for Route Point calls
- Storage full or misconfigured
- 3CX license doesn't include call recording

## Notes
- Alfred syncs recordings every 5 minutes automatically
- Recordings are streamed from 3CX (not stored in Alfred)
- Recording matching uses phone numbers from 3CX metadata
- If you change phone number formats, recordings might not link correctly
