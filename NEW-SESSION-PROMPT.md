# New Session Starter Prompt

Copy this into your next Claude Code session:

---

## Context

I'm working on Alfred (Jamal Dialler) - an on-prem telesales dashboard. Read the handoff doc first:

**@HANDOFF-RECORDING-ISSUE.md**

## Your Mission

Fix the recording issue. Recordings were working 48 hours ago but stopped. 3CX IS recording calls, but Alfred is not linking them to calls in the UI.

## Critical Rules

### 1. VERIFY BEFORE ASSUMING
- ❌ "The issue is probably X"
- ✅ "Let me check... [runs diagnostic]... The data shows X"
- Never state conclusions without evidence

### 2. USE SUBAGENTS IMMEDIATELY
When you encounter ANY technical blocker:
- Can't SSH? → Launch shell subagent with `ssh nisar@165.154.217.39`
- Need diagnostics? → Launch investigative subagent
- Multiple systems? → Launch parallel subagents

**DO NOT** ask me to run commands or copy-paste. Delegate to subagents.

### 3. WORK AUTONOMOUSLY ON VPS
- **Use VPS helper script:** `./alfred-vps.sh {logs|status|recordings|db|restart|pull}`
- Or SSH directly: `ssh nisar@165.154.217.39`
- VPS path: `~/apps/alfred`
- Commands: `docker compose logs`, `docker compose exec db psql`, etc.
- **If your SSH fails, immediately launch a subagent - don't ask me to do it**

**VPS Helper Script Commands:**
```bash
./alfred-vps.sh logs          # Last 100 API logs
./alfred-vps.sh status        # Container status  
./alfred-vps.sh recordings    # Recent calls + recording status
./alfred-vps.sh db            # Database stats (completed calls, recordings count)
./alfred-vps.sh restart       # Restart API container
./alfred-vps.sh pull          # Git pull + restart
```

### 4. COMMIT FREQUENTLY
After every fix:
```bash
git add <files>
git commit -m "Clear description"
git push origin main
# Then deploy on VPS
```

### 5. SHOW YOUR WORK
For every conclusion:
- Show the command you ran
- Show the output
- Explain what it means
- Then state conclusion

### 6. DIAGNOSTIC-FIRST APPROACH
Before fixing anything:
1. Run ALL diagnostics from handoff doc
2. Collect all data
3. Identify root cause with evidence
4. THEN propose fix

## Immediate Actions

1. **Read handoff doc completely**
2. **Run VPS diagnostics** using helper script:
   ```bash
   ./alfred-vps.sh recordings  # Check recent calls
   ./alfred-vps.sh logs        # Check for errors
   ./alfred-vps.sh db          # Database stats
   ```
   (Or SSH directly / launch subagent if script fails)
3. **Run detailed diagnostics** from handoff doc's "Next Steps"
4. **Report findings with evidence**
5. **Fix based on data, not assumptions**

## Success Criteria

- [ ] Identified root cause (with logs/data as proof)
- [ ] Implemented fix
- [ ] Tested: Make call → wait 5min → audio player appears
- [ ] Committed and deployed
- [ ] Verified in production

## Context Files & Tools

- **Handoff:** `HANDOFF-RECORDING-ISSUE.md`
- **VPS Helper:** `./alfred-vps.sh` (use this for quick diagnostics!)
- **Recording logic:** `backend/app/recordings.py`
- **Sync worker:** `backend/app/recording_sync.py`
- **VPS Direct:** `ssh nisar@165.154.217.39`, path: `~/apps/alfred`

## What's Already Fixed

✅ Ghost calls (deployed and working)
✅ UI fixes (committed)
✅ Settings (working)
✅ 3CX permissions (working)

**Only recordings are broken.** Focus there.

---

Let's fix this efficiently with data-driven investigation. No assumptions, no copy-paste, just autonomous work.
